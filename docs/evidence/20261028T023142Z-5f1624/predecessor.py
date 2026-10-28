import os
import sys
import json
import re
import hmac
import hashlib
import secrets
import sqlite3
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
SUMMARY = 'id, inbox_id, external_id, received_at, body_sha256'


def now():
    return datetime.now(timezone.utc).isoformat(timespec='microseconds').replace('+00:00', 'Z')


def digest(value):
    return hashlib.sha256(value).hexdigest()


def connect(path):
    db = sqlite3.connect(path, timeout=30)
    db.row_factory = sqlite3.Row
    db.execute('PRAGMA foreign_keys=ON')
    db.execute('PRAGMA busy_timeout=30000')
    return db


class HTTPError(Exception):
    def __init__(self, status, message):
        self.status = status
        self.message = message


class Handler(BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'
    server_version = 'Phoenix'
    sys_version = ''

    def log_message(self, *args):
        pass

    def reply(self, status, data):
        payload = json.dumps(data, ensure_ascii=True, separators=(',', ':'), allow_nan=False).encode('utf-8')
        self.close_connection = True
        self.send_response(status)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(payload)))
        self.send_header('Connection', 'close')
        self.end_headers()
        self.wfile.write(payload)

    def send_error(self, code, message=None, explain=None):
        self.reply(code, {'error': self.responses.get(code, ('Request failed',))[0]})

    def handle_expect_100(self):
        return True

    def __getattr__(self, name):
        if name.startswith('do_'):
            return self.dispatch
        raise AttributeError(name)

    def read_body(self):
        if self.headers.get_all('Transfer-Encoding') is not None:
            raise HTTPError(400, 'Transfer-Encoding is not supported')
        lengths = self.headers.get_all('Content-Length', [])
        if len(lengths) > 1:
            raise HTTPError(400, 'Invalid Content-Length')
        if lengths:
            value = lengths[0].strip()
            if not re.fullmatch(r'[0-9]+', value):
                raise HTTPError(400, 'Invalid Content-Length')
            # Bound the conversion as well as the actual allocation.
            significant = value.lstrip('0') or '0'
            if len(significant) > 5 or int(significant) > MAX_BODY:
                raise HTTPError(413, 'Body too large')
            length = int(significant)
        else:
            length = 0
        if self.headers.get('Expect', '').lower() == '100-continue':
            self.send_response_only(100)
            self.end_headers()
        raw = self.rfile.read(length)
        if len(raw) != length:
            raise HTTPError(400, 'Incomplete request body')
        try:
            body = raw.decode('utf-8')
        except UnicodeDecodeError:
            raise HTTPError(400, 'Invalid UTF-8')
        return raw, body

    def dispatch(self):
        db = None
        try:
            self.connection.settimeout(30)
            try:
                url = urlsplit(self.path)
                path = url.path
                parts = [unquote(p, encoding='utf-8', errors='strict') for p in path.split('/')]
            except (ValueError, UnicodeError):
                raise HTTPError(400, 'Invalid request target')
            if path.startswith('/api/') or (len(parts) > 1 and parts[1] == 'api'):
                values = self.headers.get_all('Authorization', [])
                expected = ('Bearer ' + self.server.admin_token).encode('utf-8')
                supplied = values[0].encode('utf-8') if len(values) == 1 else b''
                if not hmac.compare_digest(supplied, expected):
                    raise HTTPError(401, 'Unauthorized')
            raw, body = self.read_body()
            if self.command not in ('GET', 'POST'):
                raise HTTPError(405, 'Method not allowed')
            if parts == ['', 'health']:
                if self.command != 'GET':
                    raise HTTPError(405, 'Method not allowed')
                self.reply(200, {'status': 'ok', 'schema_version': 1})
                return
            db = connect(self.server.db_path)
            if parts == ['', 'api', 'inboxes']:
                if self.command == 'POST':
                    try:
                        obj = json.loads(body, parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
                    except (ValueError, RecursionError):
                        raise HTTPError(400, 'Invalid JSON')
                    if not isinstance(obj, dict) or not isinstance(obj.get('name'), str):
                        raise HTTPError(400, 'Invalid name')
                    name = obj['name'].strip()
                    if not 1 <= len(name) <= 80:
                        raise HTTPError(400, 'Invalid name')
                    try:
                        name.encode('utf-8')
                    except UnicodeEncodeError:
                        raise HTTPError(400, 'Invalid name')
                    identifier = secrets.token_urlsafe(24)
                    token = secrets.token_urlsafe(32)
                    created = now()
                    db.execute('INSERT INTO inboxes (id,name,token_sha256,created_at) VALUES (?,?,?,?)',
                               (identifier, name, digest(token.encode('utf-8')), created))
                    db.commit()
                    self.reply(201, {'id': identifier, 'name': name, 'created_at': created, 'token': token})
                else:
                    rows = db.execute('SELECT id,name,created_at FROM inboxes ORDER BY created_at DESC,id DESC').fetchall()
                    self.reply(200, {'inboxes': [dict(row) for row in rows]})
                return
            if len(parts) == 4 and parts[1] == 'hooks' and parts[2] and parts[3]:
                if self.command != 'POST':
                    raise HTTPError(405, 'Method not allowed')
                inbox_id, token = parts[2:]
                inbox = db.execute('SELECT token_sha256 FROM inboxes WHERE id=?', (inbox_id,)).fetchone()
                candidate = digest(token.encode('utf-8'))
                expected = inbox['token_sha256'] if inbox else '0' * 64
                matches = hmac.compare_digest(candidate, expected)
                if inbox is None or not matches:
                    raise HTTPError(404, 'Not found')
                headers = {}
                for key in ('content-type', 'x-webhook-id', 'x-event-type'):
                    value = self.headers.get(key)
                    if value is not None:
                        headers[key] = value
                external = headers.get('x-webhook-id') or None
                body_hash = digest(raw)
                identifier = secrets.token_urlsafe(24)
                # Serialize the key lookup and insert across connections/processes.
                db.execute('BEGIN IMMEDIATE')
                if external is not None:
                    existing = db.execute('SELECT id,body FROM events WHERE inbox_id=? AND external_id=?',
                                          (inbox_id, external)).fetchone()
                    if existing:
                        db.rollback()
                        if existing['body'] != body:
                            raise HTTPError(409, 'Idempotency key has a different body')
                        self.reply(200, {'id': existing['id'], 'duplicate': True})
                        return
                db.execute('INSERT INTO events (id,inbox_id,external_id,received_at,headers_json,body,body_sha256) VALUES (?,?,?,?,?,?,?)',
                           (identifier, inbox_id, external, now(), json.dumps(headers), body, body_hash))
                db.commit()
                self.reply(201, {'id': identifier, 'duplicate': False})
                return
            if len(parts) == 5 and parts[1:3] == ['api', 'inboxes'] and parts[3] and parts[4] == 'events':
                if self.command != 'GET':
                    raise HTTPError(405, 'Method not allowed')
                query = parse_qs(url.query, keep_blank_values=True)
                values = query.get('limit', ['20'])
                if len(values) != 1 or not re.fullmatch(r'[0-9]+', values[0]):
                    raise HTTPError(400, 'Invalid limit')
                number = values[0].lstrip('0') or '0'
                if len(number) > 3 or not 1 <= int(number) <= 100:
                    raise HTTPError(400, 'Invalid limit')
                inbox_id = parts[3]
                if db.execute('SELECT 1 FROM inboxes WHERE id=?', (inbox_id,)).fetchone() is None:
                    raise HTTPError(404, 'Not found')
                rows = db.execute('SELECT ' + SUMMARY + ' FROM events WHERE inbox_id=? ORDER BY received_at DESC,id DESC LIMIT ?',
                                  (inbox_id, int(number))).fetchall()
                self.reply(200, {'events': [dict(row) for row in rows]})
                return
            if len(parts) == 4 and parts[1:3] == ['api', 'events'] and parts[3]:
                if self.command != 'GET':
                    raise HTTPError(405, 'Method not allowed')
                row = db.execute('SELECT ' + SUMMARY + ',body,headers_json FROM events WHERE id=?', (parts[3],)).fetchone()
                if row is None:
                    raise HTTPError(404, 'Not found')
                event = dict(row)
                event['headers'] = json.loads(event.pop('headers_json'))
                self.reply(200, event)
                return
            raise HTTPError(404, 'Not found')
        except HTTPError as exc:
            self.reply(exc.status, {'error': exc.message})
        except (BrokenPipeError, ConnectionResetError):
            self.close_connection = True
        except TimeoutError:
            self.reply(400, {'error': 'Request timed out'})
        except Exception:
            self.reply(500, {'error': 'Internal server error'})
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
    try:
        port = int(os.environ.get('PORT', '8787'))
        db_path = os.environ.get('PHOENIX_DB_PATH', 'phoenix.sqlite')
        db = connect(db_path)
        try:
            db.executescript(SCHEMA)
            db.commit()
        finally:
            db.close()
        server = Server(('127.0.0.1', port), Handler)
        server.admin_token = token
        server.db_path = db_path
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
