import os
import json
import sqlite3
import secrets
import hashlib
import hmac
import re
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
    return datetime.now(timezone.utc).isoformat(timespec='microseconds').replace('+00:00', 'Z')


def digest(value):
    return hashlib.sha256(value.encode('utf-8')).hexdigest()


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


class Handler(BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'

    def log_message(self, format, *args):
        pass

    def log_error(self, format, *args):
        pass

    def setup(self):
        super().setup()
        self.connection.settimeout(30)

    def __getattr__(self, name):
        if name.startswith('do_'):
            return self.dispatch
        raise AttributeError(name)

    def send_json(self, status, payload):
        data = json.dumps(payload, ensure_ascii=True, separators=(',', ':')).encode('utf-8')
        self.close_connection = True
        self.send_response(status)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(data)))
        self.send_header('Connection', 'close')
        self.end_headers()
        if self.command != 'HEAD':
            self.wfile.write(data)

    def send_error(self, code, message=None, explain=None):
        self.send_json(code, {'error': 'HTTP request rejected'})

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
        lengths = self.headers.get_all('Content-Length')
        if lengths is None:
            return 0
        if len(lengths) != 1:
            raise Rejection(400, 'Invalid Content-Length')
        value = lengths[0].strip()
        if not re.fullmatch(r'[0-9]+', value):
            raise Rejection(400, 'Invalid Content-Length')
        canonical = value.lstrip('0') or '0'
        if len(canonical) > 5 or int(canonical) > MAX_BODY:
            raise Rejection(413, 'Body too large')
        return int(canonical)

    def read_body(self):
        length = self.body_length()
        try:
            data = self.rfile.read(length)
        except (OSError, TimeoutError):
            raise Rejection(400, 'Incomplete request body')
        if len(data) != length:
            raise Rejection(400, 'Incomplete request body')
        try:
            return data.decode('utf-8')
        except UnicodeDecodeError:
            raise Rejection(400, 'Body must be valid UTF-8')

    def dispatch(self):
        db = None
        try:
            body = self.read_body()
            try:
                url = urlsplit(self.path)
                path = url.path
                parts = [unquote(p, encoding='utf-8', errors='strict') for p in path.split('/')]
            except (ValueError, UnicodeError):
                raise Rejection(400, 'Invalid URL')
            if path.startswith('/api/'):
                credentials = self.headers.get_all('Authorization') or []
                expected = ('Bearer ' + self.server.admin_token).encode('utf-8')
                if len(credentials) != 1 or not hmac.compare_digest(credentials[0].encode('utf-8'), expected):
                    raise Rejection(401, 'Unauthorized')
            if self.command not in ('GET', 'POST'):
                raise Rejection(405, 'Method not allowed')
            if path == '/health':
                if self.command != 'GET':
                    raise Rejection(405, 'Method not allowed')
                self.send_json(200, {'status': 'ok', 'schema_version': 1})
                return
            if path == '/api/inboxes':
                db = connect(self.server.db_path)
                if self.command == 'GET':
                    rows = db.execute('SELECT id,name,created_at FROM inboxes ORDER BY created_at DESC,id DESC').fetchall()
                    self.send_json(200, {'inboxes': [dict(row) for row in rows]})
                    return
                try:
                    obj = json.loads(body)
                except (ValueError, RecursionError):
                    raise Rejection(400, 'Invalid JSON')
                if not isinstance(obj, dict) or not isinstance(obj.get('name'), str):
                    raise Rejection(400, 'Invalid name')
                name = obj['name'].strip()
                if not 1 <= len(name) <= 80:
                    raise Rejection(400, 'Invalid name')
                try:
                    name.encode('utf-8')
                except UnicodeError:
                    raise Rejection(400, 'Invalid name')
                inbox_id = secrets.token_urlsafe(24)
                token = secrets.token_urlsafe(32)
                created_at = now()
                db.execute('INSERT INTO inboxes(id,name,token_sha256,created_at) VALUES(?,?,?,?)',
                           (inbox_id, name, digest(token), created_at))
                db.commit()
                self.send_json(201, {'id': inbox_id, 'name': name, 'created_at': created_at, 'token': token})
                return
            if len(parts) == 4 and parts[0] == '' and parts[1] == 'hooks' and parts[2] and parts[3]:
                if self.command != 'POST':
                    raise Rejection(405, 'Method not allowed')
                db = connect(self.server.db_path)
                inbox = db.execute('SELECT token_sha256 FROM inboxes WHERE id=?', (parts[2],)).fetchone()
                if inbox is None or not hmac.compare_digest(inbox['token_sha256'], digest(parts[3])):
                    raise Rejection(404, 'Not found')
                captured = {}
                for key in ('content-type', 'x-webhook-id', 'x-event-type'):
                    value = self.headers.get(key)
                    if value is not None:
                        captured[key] = value
                external_id = captured.get('x-webhook-id') or None
                body_hash = digest(body)
                db.execute('BEGIN IMMEDIATE')
                if external_id is not None:
                    previous = db.execute('SELECT id,body FROM events WHERE inbox_id=? AND external_id=?',
                                          (parts[2], external_id)).fetchone()
                    if previous is not None:
                        db.commit()
                        if previous['body'] != body:
                            raise Rejection(409, 'Idempotency key already used with a different body')
                        self.send_json(200, {'id': previous['id'], 'duplicate': True})
                        return
                event_id = secrets.token_urlsafe(24)
                db.execute('INSERT INTO events(id,inbox_id,external_id,received_at,headers_json,body,body_sha256) VALUES(?,?,?,?,?,?,?)',
                           (event_id, parts[2], external_id, now(), json.dumps(captured), body, body_hash))
                db.commit()
                self.send_json(201, {'id': event_id, 'duplicate': False})
                return
            if len(parts) == 5 and parts[:3] == ['', 'api', 'inboxes'] and parts[3] and parts[4] == 'events':
                if self.command != 'GET':
                    raise Rejection(405, 'Method not allowed')
                query = parse_qs(url.query, keep_blank_values=True)
                values = query.get('limit', ['20'])
                if len(values) != 1 or not re.fullmatch(r'[0-9]+', values[0]):
                    raise Rejection(400, 'Invalid limit')
                value = values[0].lstrip('0') or '0'
                if len(value) > 3 or not 1 <= int(value) <= 100:
                    raise Rejection(400, 'Invalid limit')
                db = connect(self.server.db_path)
                if db.execute('SELECT 1 FROM inboxes WHERE id=?', (parts[3],)).fetchone() is None:
                    raise Rejection(404, 'Not found')
                rows = db.execute('SELECT id,inbox_id,external_id,received_at,body_sha256 FROM events WHERE inbox_id=? ORDER BY received_at DESC,id DESC LIMIT ?',
                                  (parts[3], int(value))).fetchall()
                self.send_json(200, {'events': [dict(row) for row in rows]})
                return
            if len(parts) == 4 and parts[:3] == ['', 'api', 'events'] and parts[3]:
                if self.command != 'GET':
                    raise Rejection(405, 'Method not allowed')
                db = connect(self.server.db_path)
                row = db.execute('SELECT id,inbox_id,external_id,received_at,body_sha256,body,headers_json FROM events WHERE id=?',
                                 (parts[3],)).fetchone()
                if row is None:
                    raise Rejection(404, 'Not found')
                event = dict(row)
                event['headers'] = json.loads(event.pop('headers_json'))
                self.send_json(200, event)
                return
            raise Rejection(404, 'Not found')
        except Rejection as exc:
            self.send_json(exc.status, {'error': exc.message})
        except (BrokenPipeError, ConnectionResetError):
            self.close_connection = True
        except Exception:
            self.send_json(500, {'error': 'Internal server error'})
        finally:
            if db is not None:
                db.close()


class Server(ThreadingHTTPServer):
    daemon_threads = True

    def handle_error(self, request, client_address):
        pass


def main():
    token = os.environ.get('PHOENIX_ADMIN_TOKEN')
    if not token:
        raise SystemExit('PHOENIX_ADMIN_TOKEN is required')
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
    except Exception:
        raise SystemExit('Unable to initialize service')
    server.admin_token = token
    server.db_path = path
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == '__main__':
    main()
