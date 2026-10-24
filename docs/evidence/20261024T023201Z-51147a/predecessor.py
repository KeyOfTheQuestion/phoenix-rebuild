import hashlib
import hmac
import json
import os
import re
import secrets
import sqlite3
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, unquote, urlsplit


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
    return datetime.now(timezone.utc).isoformat(timespec='microseconds')


def connect(path):
    db = sqlite3.connect(path, timeout=30)
    db.row_factory = sqlite3.Row
    db.execute('PRAGMA foreign_keys=ON')
    db.execute('PRAGMA busy_timeout=30000')
    return db


class Rejection(Exception):
    def __init__(self, status, message):
        self.status = status
        self.message = message


class Server(ThreadingHTTPServer):
    daemon_threads = True

    def handle_error(self, request, client_address):
        # Never print request context or exception traces.
        pass


class Handler(BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'

    def log_message(self, format, *args):
        pass

    def send_json(self, status, data):
        payload = json.dumps(data, ensure_ascii=True, separators=(',', ':')).encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(payload)))
        self.send_header('Connection', 'close')
        self.end_headers()
        self.close_connection = True
        self.wfile.write(payload)

    def send_error(self, code, message=None, explain=None):
        self.send_json(code, {'error': 'Invalid HTTP request'})

    def __getattr__(self, name):
        if name.startswith('do_'):
            return self.dispatch
        raise AttributeError(name)

    def handle_expect_100(self):
        try:
            self.body_length()
        except Rejection as exc:
            self.send_json(exc.status, {'error': exc.message})
            return False
        self.send_response_only(100)
        self.end_headers()
        return True

    def body_length(self):
        if self.headers.get_all('Transfer-Encoding') is not None:
            raise Rejection(400, 'Transfer-Encoding is not supported')
        values = self.headers.get_all('Content-Length', [])
        if not values:
            return 0
        if len(values) != 1:
            raise Rejection(400, 'Invalid Content-Length')
        value = values[0].strip()
        if not re.fullmatch(r'[0-9]+', value):
            raise Rejection(400, 'Invalid Content-Length')
        significant = value.lstrip('0') or '0'
        if len(significant) > 5:
            raise Rejection(413, 'Body too large')
        length = int(significant)
        if length > MAX_BODY:
            raise Rejection(413, 'Body too large')
        return length

    def dispatch(self):
        db = None
        self.close_connection = True
        try:
            self.connection.settimeout(30)
            length = self.body_length()
            try:
                target = urlsplit(self.path)
                path = unquote(target.path, encoding='utf-8', errors='strict')
            except (ValueError, UnicodeError):
                raise Rejection(400, 'Invalid URL')

            if path.startswith('/api/') or path == '/api':
                values = self.headers.get_all('Authorization', [])
                supplied = values[0] if len(values) == 1 else ''
                expected = 'Bearer ' + self.server.admin_token
                if not hmac.compare_digest(supplied.encode('utf-8'), expected.encode('utf-8')):
                    raise Rejection(401, 'Unauthorized')

            parts = path.split('/')
            route = None
            method = None
            if path == '/health':
                route, method = 'health', 'GET'
            elif path == '/api/inboxes':
                route, method = 'inboxes', ('GET', 'POST')
            elif len(parts) == 5 and parts[1:2] == ['hooks'] and False:
                pass
            elif len(parts) == 4 and parts[1] == 'hooks' and parts[2] and parts[3]:
                route, method = 'hook', 'POST'
            elif len(parts) == 5 and parts[1:3] == ['api', 'inboxes'] and parts[3] and parts[4] == 'events':
                route, method = 'events', 'GET'
            elif len(parts) == 4 and parts[1:3] == ['api', 'events'] and parts[3]:
                route, method = 'event', 'GET'

            if self.command not in ('GET', 'POST'):
                raise Rejection(405, 'Method not allowed')
            if route is None:
                raise Rejection(404, 'Not found')
            allowed = (method,) if isinstance(method, str) else method
            if self.command not in allowed:
                raise Rejection(405, 'Method not allowed')

            try:
                raw = self.rfile.read(length)
            except (OSError, TimeoutError):
                raise Rejection(400, 'Incomplete body')
            if len(raw) != length:
                raise Rejection(400, 'Incomplete body')
            try:
                body = raw.decode('utf-8')
            except UnicodeDecodeError:
                raise Rejection(400, 'Body must be UTF-8')

            if route == 'health':
                self.send_json(200, {'status': 'ok', 'schema_version': 1})
                return

            db = connect(self.server.db_path)
            if route == 'inboxes' and self.command == 'POST':
                try:
                    data = json.loads(body)
                except (ValueError, RecursionError):
                    raise Rejection(400, 'Invalid JSON')
                if not isinstance(data, dict) or not isinstance(data.get('name'), str):
                    raise Rejection(400, 'A name string is required')
                name = data['name'].strip()
                if not 1 <= len(name) <= 80:
                    raise Rejection(400, 'Name must contain 1 to 80 characters')
                try:
                    name.encode('utf-8')
                except UnicodeEncodeError:
                    raise Rejection(400, 'Invalid name')
                inbox_id = secrets.token_urlsafe(24)
                token = secrets.token_urlsafe(32)
                created_at = now()
                digest = hashlib.sha256(token.encode('utf-8')).hexdigest()
                db.execute('INSERT INTO inboxes (id,name,token_sha256,created_at) VALUES (?,?,?,?)',
                           (inbox_id, name, digest, created_at))
                db.commit()
                self.send_json(201, {'id': inbox_id, 'name': name, 'created_at': created_at, 'token': token})
            elif route == 'inboxes':
                rows = db.execute('SELECT id,name,created_at FROM inboxes ORDER BY created_at DESC,id DESC').fetchall()
                self.send_json(200, {'inboxes': [dict(row) for row in rows]})
            elif route == 'hook':
                inbox_id, token = parts[2], parts[3]
                row = db.execute('SELECT token_sha256 FROM inboxes WHERE id=?', (inbox_id,)).fetchone()
                digest = hashlib.sha256(token.encode('utf-8')).hexdigest()
                stored = row['token_sha256'] if row else '0' * 64
                matches = hmac.compare_digest(digest.encode('ascii'), stored.encode('utf-8'))
                if row is None or not matches:
                    raise Rejection(404, 'Not found')
                headers = {}
                for key in ('content-type', 'x-webhook-id', 'x-event-type'):
                    value = self.headers.get(key)
                    if value is not None:
                        headers[key] = value
                external_id = headers.get('x-webhook-id') or None
                body_hash = hashlib.sha256(raw).hexdigest()
                db.execute('BEGIN IMMEDIATE')
                if external_id is not None:
                    previous = db.execute('SELECT id,body FROM events WHERE inbox_id=? AND external_id=?',
                                          (inbox_id, external_id)).fetchone()
                    if previous is not None:
                        if previous['body'] != body:
                            raise Rejection(409, 'Idempotency key already used with a different body')
                        db.commit()
                        self.send_json(200, {'id': previous['id'], 'duplicate': True})
                        return
                event_id = secrets.token_urlsafe(24)
                db.execute('INSERT INTO events (id,inbox_id,external_id,received_at,headers_json,body,body_sha256) VALUES (?,?,?,?,?,?,?)',
                           (event_id, inbox_id, external_id, now(), json.dumps(headers), body, body_hash))
                db.commit()
                self.send_json(201, {'id': event_id, 'duplicate': False})
            elif route == 'events':
                query = parse_qs(target.query, keep_blank_values=True)
                limits = query.get('limit', [])
                limit = 20
                if limits:
                    if len(limits) != 1 or not re.fullmatch(r'[0-9]+', limits[0]):
                        raise Rejection(400, 'Invalid limit')
                    value = limits[0].lstrip('0') or '0'
                    if len(value) > 3 or not 1 <= int(value) <= 100:
                        raise Rejection(400, 'Invalid limit')
                    limit = int(value)
                inbox_id = parts[3]
                if db.execute('SELECT 1 FROM inboxes WHERE id=?', (inbox_id,)).fetchone() is None:
                    raise Rejection(404, 'Not found')
                rows = db.execute('SELECT ' + SUMMARY + ' FROM events WHERE inbox_id=? ORDER BY received_at DESC,id DESC LIMIT ?',
                                  (inbox_id, limit)).fetchall()
                self.send_json(200, {'events': [dict(row) for row in rows]})
            elif route == 'event':
                row = db.execute('SELECT ' + SUMMARY + ',body,headers_json FROM events WHERE id=?', (parts[3],)).fetchone()
                if row is None:
                    raise Rejection(404, 'Not found')
                result = dict(row)
                result['headers'] = json.loads(result.pop('headers_json'))
                self.send_json(200, result)
        except Rejection as exc:
            self.send_json(exc.status, {'error': exc.message})
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception:
            self.send_json(500, {'error': 'Internal server error'})
        finally:
            if db is not None:
                db.close()


def main():
    token = os.environ.get('PHOENIX_ADMIN_TOKEN')
    if not token:
        raise SystemExit('PHOENIX_ADMIN_TOKEN is required')
    try:
        port = int(os.environ.get('PORT', '8787'))
        if not 0 <= port <= 65535:
            raise ValueError()
    except ValueError:
        raise SystemExit('Invalid PORT')
    db_path = os.environ.get('PHOENIX_DB_PATH', 'phoenix.sqlite')
    try:
        db = connect(db_path)
        try:
            db.executescript(SCHEMA)
            db.commit()
        finally:
            db.close()
        server = Server(('127.0.0.1', port), Handler)
    except Exception:
        raise SystemExit('Unable to initialize service')
    server.admin_token = token
    server.db_path = db_path
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == '__main__':
    main()
