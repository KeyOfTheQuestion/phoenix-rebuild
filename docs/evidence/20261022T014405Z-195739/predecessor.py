import hashlib
import hmac
import json
import os
import re
import secrets
import sqlite3
import sys
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
SUMMARY_COLUMNS = 'id, inbox_id, external_id, received_at, body_sha256'


def timestamp():
    return datetime.now(timezone.utc).isoformat(timespec='microseconds').replace('+00:00', 'Z')


def connect(path):
    db = sqlite3.connect(path, timeout=30)
    db.row_factory = sqlite3.Row
    db.execute('PRAGMA foreign_keys=ON')
    db.execute('PRAGMA busy_timeout=30000')
    return db


class RequestError(Exception):
    def __init__(self, status, message):
        self.status = status
        self.message = message


class Handler(BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.0'
    server_version = 'Phoenix/1.0'
    sys_version = ''

    def log_message(self, format, *args):
        pass

    def send_error(self, code, message=None, explain=None):
        safe_messages = {
            400: 'Bad request', 404: 'Not found', 405: 'Method not allowed',
            408: 'Request timeout', 413: 'Body too large',
            414: 'Request URI too long', 431: 'Request headers too large',
            501: 'Method not allowed', 505: 'HTTP version not supported'
        }
        if code == 501:
            code = 405
        self.respond(code, {'error': safe_messages.get(code, 'Request failed')})

    def respond(self, status, payload):
        data = json.dumps(payload, ensure_ascii=True, separators=(',', ':')).encode('utf-8')
        self.close_connection = True
        self.send_response(status)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(data)))
        self.send_header('Connection', 'close')
        self.end_headers()
        if self.command != 'HEAD':
            self.wfile.write(data)

    def __getattr__(self, name):
        if name.startswith('do_'):
            return self.dispatch
        raise AttributeError(name)

    def read_body(self):
        if self.headers.get_all('Transfer-Encoding') is not None:
            raise RequestError(400, 'Transfer-Encoding is not supported')
        lengths = self.headers.get_all('Content-Length')
        if lengths is None:
            length = 0
        else:
            if len(lengths) != 1:
                raise RequestError(400, 'Invalid Content-Length')
            value = lengths[0].strip()
            if not re.fullmatch(r'[0-9]+', value):
                raise RequestError(400, 'Invalid Content-Length')
            significant = value.lstrip('0') or '0'
            if len(significant) > 5 or int(significant) > MAX_BODY:
                raise RequestError(413, 'Body too large')
            length = int(significant)
        try:
            raw = self.rfile.read(length) if length else b''
        except (TimeoutError, OSError):
            raise RequestError(400, 'Incomplete request body')
        if len(raw) != length:
            raise RequestError(400, 'Incomplete request body')
        try:
            return raw, raw.decode('utf-8')
        except UnicodeDecodeError:
            raise RequestError(400, 'Body must be valid UTF-8')

    def require_admin(self):
        values = self.headers.get_all('Authorization') or []
        supplied = values[0].encode('utf-8') if len(values) == 1 else b''
        if not hmac.compare_digest(supplied, self.server.admin_authorization):
            raise RequestError(401, 'Unauthorized')

    def dispatch(self):
        db = None
        self.close_connection = True
        try:
            try:
                parsed = urlsplit(self.path)
                path = unquote(parsed.path, encoding='utf-8', errors='strict')
            except (ValueError, UnicodeError):
                raise RequestError(400, 'Invalid request URL')
            if path.startswith('/api/') or path == '/api':
                self.require_admin()
            raw, body = self.read_body()
            if self.command not in ('GET', 'POST'):
                raise RequestError(405, 'Method not allowed')

            route = None
            params = None
            if path == '/health':
                route = 'health'
                allowed = ('GET',)
            elif path == '/api/inboxes':
                route = 'inboxes'
                allowed = ('GET', 'POST')
            else:
                params = re.fullmatch(r'/hooks/([^/]+)/([^/]+)', path)
                if params:
                    route = 'hook'
                    allowed = ('POST',)
                else:
                    params = re.fullmatch(r'/api/inboxes/([^/]+)/events', path)
                    if params:
                        route = 'events'
                        allowed = ('GET',)
                    else:
                        params = re.fullmatch(r'/api/events/([^/]+)', path)
                        if params:
                            route = 'event'
                            allowed = ('GET',)
            if route is None:
                raise RequestError(404, 'Not found')
            if self.command not in allowed:
                raise RequestError(405, 'Method not allowed')
            if route == 'health':
                self.respond(200, {'status': 'ok', 'schema_version': 1})
                return

            db = connect(self.server.db_path)
            if route == 'inboxes':
                if self.command == 'GET':
                    rows = db.execute('SELECT id, name, created_at FROM inboxes ORDER BY created_at DESC, id DESC').fetchall()
                    self.respond(200, {'inboxes': [dict(row) for row in rows]})
                    return
                try:
                    obj = json.loads(body, parse_constant=self.reject_json_constant)
                except (ValueError, RecursionError):
                    raise RequestError(400, 'Invalid JSON object')
                if not isinstance(obj, dict) or not isinstance(obj.get('name'), str):
                    raise RequestError(400, 'Name must be a string')
                name = obj['name'].strip()
                if not 1 <= len(name) <= 80:
                    raise RequestError(400, 'Name must contain 1 to 80 characters')
                try:
                    name.encode('utf-8')
                except UnicodeEncodeError:
                    raise RequestError(400, 'Name must be valid Unicode')
                inbox_id = secrets.token_urlsafe(24)
                token = secrets.token_urlsafe(32)
                created_at = timestamp()
                digest = hashlib.sha256(token.encode('utf-8')).hexdigest()
                db.execute('INSERT INTO inboxes (id, name, token_sha256, created_at) VALUES (?, ?, ?, ?)',
                           (inbox_id, name, digest, created_at))
                db.commit()
                self.respond(201, {'id': inbox_id, 'name': name, 'created_at': created_at, 'token': token})
                return

            if route == 'hook':
                inbox_id, token = params.groups()
                inbox = db.execute('SELECT token_sha256 FROM inboxes WHERE id = ?', (inbox_id,)).fetchone()
                digest = hashlib.sha256(token.encode('utf-8')).hexdigest()
                stored = inbox['token_sha256'] if inbox is not None else '0' * 64
                valid = hmac.compare_digest(digest.encode('ascii'), stored.encode('utf-8'))
                if inbox is None or not valid:
                    raise RequestError(404, 'Not found')
                headers = {}
                for key in ('content-type', 'x-webhook-id', 'x-event-type'):
                    value = self.headers.get(key)
                    if value is not None:
                        headers[key] = value
                external_id = headers.get('x-webhook-id') or None
                body_digest = hashlib.sha256(raw).hexdigest()
                db.execute('BEGIN IMMEDIATE')
                if external_id is not None:
                    existing = db.execute('SELECT id, body FROM events WHERE inbox_id = ? AND external_id = ?',
                                          (inbox_id, external_id)).fetchone()
                    if existing is not None:
                        if existing['body'] != body:
                            db.rollback()
                            raise RequestError(409, 'Idempotency key already used with a different body')
                        db.commit()
                        self.respond(200, {'id': existing['id'], 'duplicate': True})
                        return
                event_id = secrets.token_urlsafe(24)
                db.execute('INSERT INTO events (id, inbox_id, external_id, received_at, headers_json, body, body_sha256) VALUES (?, ?, ?, ?, ?, ?, ?)',
                           (event_id, inbox_id, external_id, timestamp(), json.dumps(headers, ensure_ascii=True), body, body_digest))
                db.commit()
                self.respond(201, {'id': event_id, 'duplicate': False})
                return

            if route == 'events':
                query = parse_qs(parsed.query, keep_blank_values=True)
                values = query.get('limit', ['20'])
                if len(values) != 1 or not re.fullmatch(r'[0-9]+', values[0]):
                    raise RequestError(400, 'Invalid limit')
                significant = values[0].lstrip('0') or '0'
                if len(significant) > 3 or not 1 <= int(significant) <= 100:
                    raise RequestError(400, 'Limit must be between 1 and 100')
                limit = int(significant)
                inbox_id = params.group(1)
                if db.execute('SELECT id FROM inboxes WHERE id = ?', (inbox_id,)).fetchone() is None:
                    raise RequestError(404, 'Not found')
                rows = db.execute('SELECT ' + SUMMARY_COLUMNS + ' FROM events WHERE inbox_id = ? ORDER BY received_at DESC, id DESC LIMIT ?',
                                  (inbox_id, limit)).fetchall()
                self.respond(200, {'events': [dict(row) for row in rows]})
                return

            row = db.execute('SELECT ' + SUMMARY_COLUMNS + ', body, headers_json FROM events WHERE id = ?',
                             (params.group(1),)).fetchone()
            if row is None:
                raise RequestError(404, 'Not found')
            event = dict(row)
            event['headers'] = json.loads(event.pop('headers_json'))
            self.respond(200, event)
        except RequestError as exc:
            self.respond(exc.status, {'error': exc.message})
        except (BrokenPipeError, ConnectionResetError, TimeoutError):
            pass
        except Exception:
            self.respond(500, {'error': 'Internal server error'})
        finally:
            if db is not None:
                db.close()

    @staticmethod
    def reject_json_constant(value):
        raise ValueError('Invalid JSON constant')


class Server(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def get_request(self):
        sock, address = super().get_request()
        sock.settimeout(30)
        return sock, address

    def handle_error(self, request, client_address):
        pass


def main():
    token = os.environ.get('PHOENIX_ADMIN_TOKEN')
    if not token:
        print('PHOENIX_ADMIN_TOKEN is required and must not be empty.', file=sys.stderr)
        return 1
    db_path = os.environ.get('PHOENIX_DB_PATH', 'phoenix.sqlite')
    try:
        port = int(os.environ.get('PORT', '8787'))
        db = connect(db_path)
        try:
            db.executescript(SCHEMA)
            db.commit()
        finally:
            db.close()
        server = Server(('127.0.0.1', port), Handler)
        server.db_path = db_path
        server.admin_authorization = ('Bearer ' + token).encode('utf-8')
    except Exception:
        print('Unable to initialize service.', file=sys.stderr)
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
