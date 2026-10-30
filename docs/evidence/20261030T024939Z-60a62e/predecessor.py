import os
import sys
import json
import sqlite3
import secrets
import hashlib
import hmac
import re
import socket
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit, unquote, parse_qs

MAX_BODY = 65536
SCHEMA = '''
CREATE TABLE IF NOT EXISTS inboxes (
  id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  token_sha256 TEXT NOT NULL,
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS events (
  id TEXT PRIMARY KEY,
  inbox_id TEXT NOT NULL REFERENCES inboxes(id),
  external_id TEXT,
  received_at TEXT NOT NULL,
  headers_json TEXT NOT NULL,
  body TEXT NOT NULL,
  body_sha256 TEXT NOT NULL,
  UNIQUE(inbox_id, external_id)
);
'''


def now():
    return datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')


def digest(value):
    return hashlib.sha256(value).hexdigest()


def connect(path):
    db = sqlite3.connect(path, timeout=15)
    db.row_factory = sqlite3.Row
    db.execute('PRAGMA foreign_keys=ON')
    db.execute('PRAGMA busy_timeout=15000')
    return db


class HTTPError(Exception):
    def __init__(self, status, message):
        self.status = status
        self.message = message


class Handler(BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'
    server_version = 'Phoenix'
    sys_version = ''

    def log_message(self, format, *args):
        pass

    def log_error(self, format, *args):
        pass

    def setup(self):
        super().setup()
        self.connection.settimeout(15)

    def __getattr__(self, name):
        if name.startswith('do_'):
            return self.dispatch
        raise AttributeError(name)

    def send_error(self, code, message=None, explain=None):
        self.close_connection = True
        self.respond(code, {'error': self.responses.get(code, ('Request failed',))[0]})

    def handle_expect_100(self):
        try:
            self.body_length()
        except HTTPError as exc:
            self.close_connection = True
            self.respond(exc.status, {'error': exc.message})
            return False
        self.send_response_only(100)
        self.end_headers()
        return True

    def respond(self, status, value):
        data = json.dumps(value, ensure_ascii=True, separators=(',', ':')).encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(data)))
        if self.close_connection:
            self.send_header('Connection', 'close')
        self.end_headers()
        self.wfile.write(data)

    def body_length(self):
        if self.headers.get_all('Transfer-Encoding') is not None:
            raise HTTPError(400, 'Transfer-Encoding is not supported')
        lengths = self.headers.get_all('Content-Length', [])
        if not lengths:
            if self.command == 'POST' or self.command in ('GET', 'HEAD', 'OPTIONS', 'DELETE'):
                return 0
            raise HTTPError(400, 'Missing Content-Length')
        if len(lengths) != 1 or re.fullmatch(r'[0-9]+', lengths[0]) is None:
            raise HTTPError(400, 'Invalid Content-Length')
        # Avoid integer conversion limits on maliciously long headers.
        significant = lengths[0].lstrip('0') or '0'
        if len(significant) > 5:
            raise HTTPError(413, 'Body too large')
        length = int(significant)
        if length > MAX_BODY:
            raise HTTPError(413, 'Body too large')
        return length

    def authenticate(self):
        values = self.headers.get_all('Authorization', [])
        supplied = values[0].encode('utf-8') if len(values) == 1 else b''
        expected = b'Bearer ' + self.server.admin_token.encode('utf-8')
        if not hmac.compare_digest(supplied, expected):
            raise HTTPError(401, 'Unauthorized')

    def dispatch(self):
        db = None
        try:
            length = self.body_length()
            raw = self.rfile.read(length)
            if len(raw) != length:
                raise HTTPError(400, 'Incomplete body')
            try:
                body = raw.decode('utf-8')
            except UnicodeDecodeError:
                raise HTTPError(400, 'Invalid UTF-8')
            try:
                url = urlsplit(self.path)
                path = url.path
                parts = [unquote(p, encoding='utf-8', errors='strict') for p in path.split('/')]
            except (ValueError, UnicodeError):
                raise HTTPError(400, 'Invalid request target')
            if path.startswith('/api/'):
                self.authenticate()
            method = self.command
            if method not in ('GET', 'POST'):
                raise HTTPError(405, 'Method not allowed')

            if path == '/health':
                if method != 'GET':
                    raise HTTPError(405, 'Method not allowed')
                self.respond(200, {'status': 'ok', 'schema_version': 1})
                return

            is_inboxes = path == '/api/inboxes'
            is_hook = len(parts) == 4 and parts[1] == 'hooks' and all(parts[2:])
            is_list = len(parts) == 5 and parts[1:3] == ['api', 'inboxes'] and parts[3] and parts[4] == 'events'
            is_event = len(parts) == 4 and parts[1:3] == ['api', 'events'] and parts[3]
            if not any((is_inboxes, is_hook, is_list, is_event)):
                raise HTTPError(404, 'Not found')
            if (is_hook and method != 'POST') or ((is_list or is_event) and method != 'GET'):
                raise HTTPError(405, 'Method not allowed')

            db = connect(self.server.db_path)
            if is_inboxes:
                if method == 'GET':
                    rows = db.execute('SELECT id, name, created_at FROM inboxes ORDER BY created_at DESC, id DESC').fetchall()
                    self.respond(200, {'inboxes': [dict(row) for row in rows]})
                    return
                try:
                    obj = json.loads(body)
                except (ValueError, RecursionError):
                    raise HTTPError(400, 'Invalid JSON')
                if not isinstance(obj, dict) or not isinstance(obj.get('name'), str):
                    raise HTTPError(400, 'Invalid name')
                name = obj['name'].strip()
                if not 1 <= len(name) <= 80:
                    raise HTTPError(400, 'Invalid name')
                try:
                    name.encode('utf-8')
                except UnicodeError:
                    raise HTTPError(400, 'Invalid name')
                inbox_id, token, created = secrets.token_urlsafe(24), secrets.token_urlsafe(32), now()
                db.execute('INSERT INTO inboxes (id,name,token_sha256,created_at) VALUES (?,?,?,?)',
                           (inbox_id, name, digest(token.encode('utf-8')), created))
                db.commit()
                self.respond(201, {'id': inbox_id, 'name': name, 'created_at': created, 'token': token})
                return

            if is_hook:
                inbox_id, token = parts[2:4]
                row = db.execute('SELECT token_sha256 FROM inboxes WHERE id=?', (inbox_id,)).fetchone()
                token_hash = digest(token.encode('utf-8'))
                expected_hash = row['token_sha256'] if row else '0' * 64
                matches = hmac.compare_digest(token_hash, expected_hash)
                if row is None or not matches:
                    raise HTTPError(404, 'Not found')
                headers = {}
                for key in ('content-type', 'x-webhook-id', 'x-event-type'):
                    value = self.headers.get(key)
                    if value is not None:
                        headers[key] = value
                external_id = headers.get('x-webhook-id') or None
                body_hash = digest(raw)
                db.execute('BEGIN IMMEDIATE')
                if external_id is not None:
                    existing = db.execute('SELECT id,body,body_sha256 FROM events WHERE inbox_id=? AND external_id=?',
                                          (inbox_id, external_id)).fetchone()
                    if existing:
                        if existing['body_sha256'] != body_hash or existing['body'] != body:
                            raise HTTPError(409, 'Idempotency key already used with a different body')
                        db.commit()
                        self.respond(200, {'id': existing['id'], 'duplicate': True})
                        return
                event_id = secrets.token_urlsafe(24)
                db.execute('INSERT INTO events (id,inbox_id,external_id,received_at,headers_json,body,body_sha256) VALUES (?,?,?,?,?,?,?)',
                           (event_id, inbox_id, external_id, now(), json.dumps(headers), body, body_hash))
                db.commit()
                self.respond(201, {'id': event_id, 'duplicate': False})
                return

            summary = 'id,inbox_id,external_id,received_at,body_sha256'
            if is_list:
                query = parse_qs(url.query, keep_blank_values=True)
                values = query.get('limit', ['20'])
                if len(values) != 1 or re.fullmatch(r'[0-9]+', values[0]) is None:
                    raise HTTPError(400, 'Invalid limit')
                normalized = values[0].lstrip('0') or '0'
                if len(normalized) > 3 or not 1 <= int(normalized) <= 100:
                    raise HTTPError(400, 'Invalid limit')
                inbox_id = parts[3]
                if db.execute('SELECT 1 FROM inboxes WHERE id=?', (inbox_id,)).fetchone() is None:
                    raise HTTPError(404, 'Not found')
                rows = db.execute('SELECT ' + summary + ' FROM events WHERE inbox_id=? ORDER BY received_at DESC,id DESC LIMIT ?',
                                  (inbox_id, int(normalized))).fetchall()
                self.respond(200, {'events': [dict(row) for row in rows]})
                return
            row = db.execute('SELECT ' + summary + ',body,headers_json FROM events WHERE id=?', (parts[3],)).fetchone()
            if row is None:
                raise HTTPError(404, 'Not found')
            result = dict(row)
            result['headers'] = json.loads(result.pop('headers_json'))
            self.respond(200, result)
        except HTTPError as exc:
            self.close_connection = True
            self.respond(exc.status, {'error': exc.message})
        except (socket.timeout, TimeoutError):
            self.close_connection = True
            try:
                self.respond(400, {'error': 'Request timed out'})
            except OSError:
                pass
        except (BrokenPipeError, ConnectionResetError):
            self.close_connection = True
        except Exception:
            self.close_connection = True
            try:
                self.respond(500, {'error': 'Internal server error'})
            except OSError:
                pass
        finally:
            if db is not None:
                db.close()


class Server(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def handle_error(self, request, client_address):
        pass


def main():
    token = os.environ.get('PHOENIX_ADMIN_TOKEN')
    if not token:
        sys.stderr.write('PHOENIX_ADMIN_TOKEN is required\n')
        return 1
    path = os.environ.get('PHOENIX_DB_PATH', 'phoenix.sqlite')
    try:
        port = int(os.environ.get('PORT', '8787'))
        db = connect(path)
        try:
            db.executescript(SCHEMA)
            db.commit()
        finally:
            db.close()
        server = Server(('127.0.0.1', port), Handler)
        server.admin_token = token
        server.db_path = path
    except Exception:
        sys.stderr.write('Unable to initialize service\n')
        return 1
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == '__main__':
    sys.exit(main())
