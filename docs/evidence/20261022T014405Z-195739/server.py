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


def timestamp():
    return datetime.now(timezone.utc).isoformat(timespec='microseconds')


def connect(path):
    db = sqlite3.connect(path, timeout=15)
    db.row_factory = sqlite3.Row
    db.execute('PRAGMA foreign_keys=ON')
    db.execute('PRAGMA busy_timeout=15000')
    return db


class Rejection(Exception):
    def __init__(self, status, message):
        self.status = status
        self.message = message


class Handler(BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.0'

    def log_message(self, format, *args):
        pass

    def log_error(self, format, *args):
        pass

    def send_error(self, code, message=None, explain=None):
        self.close_connection = True
        self.respond(code, {'error': self.responses.get(code, ('Request rejected',))[0]})

    def respond(self, status, data):
        payload = json.dumps(data, ensure_ascii=True, separators=(',', ':')).encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(payload)))
        self.send_header('Connection', 'close')
        self.end_headers()
        self.close_connection = True
        if self.command != 'HEAD':
            self.wfile.write(payload)

    def __getattr__(self, name):
        if name.startswith('do_'):
            return self.dispatch
        raise AttributeError(name)

    def read_body(self):
        if self.headers.get_all('Transfer-Encoding') is not None:
            raise Rejection(400, 'Transfer-Encoding is not supported')
        lengths = self.headers.get_all('Content-Length')
        if lengths is None:
            length = 0
        else:
            if len(lengths) != 1 or re.fullmatch(r'[0-9]+', lengths[0].strip()) is None:
                raise Rejection(400, 'Invalid Content-Length')
            digits = lengths[0].strip().lstrip('0') or '0'
            if len(digits) > 5:
                raise Rejection(413, 'Body too large')
            length = int(digits)
        if length > MAX_BODY:
            raise Rejection(413, 'Body too large')
        try:
            raw = self.rfile.read(length) if length else b''
        except (OSError, TimeoutError):
            raise Rejection(400, 'Incomplete request body')
        if len(raw) != length:
            raise Rejection(400, 'Incomplete request body')
        try:
            body = raw.decode('utf-8')
        except UnicodeDecodeError:
            raise Rejection(400, 'Body must be valid UTF-8')
        return raw, body

    def dispatch(self):
        db = None
        self.close_connection = True
        try:
            self.connection.settimeout(15)
            raw, body = self.read_body()
            try:
                url = urlsplit(self.path)
                path = unquote(url.path, encoding='utf-8', errors='strict')
            except (ValueError, UnicodeError):
                raise Rejection(400, 'Invalid request URL')

            if path.startswith('/api/') or path == '/api':
                auth = self.headers.get_all('Authorization') or []
                expected = ('Bearer ' + self.server.admin_token).encode('utf-8')
                supplied = auth[0].encode('utf-8') if len(auth) == 1 else b''
                if not hmac.compare_digest(supplied, expected):
                    raise Rejection(401, 'Unauthorized')

            if self.command not in ('GET', 'POST'):
                raise Rejection(405, 'Method not allowed')

            if path == '/health':
                if self.command != 'GET':
                    raise Rejection(405, 'Method not allowed')
                self.respond(200, {'status': 'ok', 'schema_version': 1})
                return

            parts = path.split('/')
            is_inboxes = path == '/api/inboxes'
            is_event_list = (len(parts) == 5 and parts[1:3] == ['api', 'inboxes']
                             and bool(parts[3]) and parts[4] == 'events')
            is_event = (len(parts) == 4 and parts[1:3] == ['api', 'events']
                        and bool(parts[3]))
            is_hook = (len(parts) == 4 and parts[1] == 'hooks'
                       and bool(parts[2]) and bool(parts[3]))
            if not any((is_inboxes, is_event_list, is_event, is_hook)):
                raise Rejection(404, 'Not found')
            if (is_event_list or is_event) and self.command != 'GET':
                raise Rejection(405, 'Method not allowed')
            if is_hook and self.command != 'POST':
                raise Rejection(405, 'Method not allowed')

            db = connect(self.server.db_path)
            if is_inboxes and self.command == 'POST':
                try:
                    data = json.loads(body)
                except (ValueError, RecursionError):
                    raise Rejection(400, 'Expected a JSON object')
                if not isinstance(data, dict) or not isinstance(data.get('name'), str):
                    raise Rejection(400, 'Invalid name')
                name = data['name'].strip()
                if not 1 <= len(name) <= 80:
                    raise Rejection(400, 'Invalid name')
                try:
                    name.encode('utf-8')
                except UnicodeEncodeError:
                    raise Rejection(400, 'Invalid name')
                inbox_id = secrets.token_urlsafe(24)
                token = secrets.token_urlsafe(32)
                created_at = timestamp()
                digest = hashlib.sha256(token.encode('utf-8')).hexdigest()
                db.execute('INSERT INTO inboxes (id, name, token_sha256, created_at) VALUES (?, ?, ?, ?)',
                           (inbox_id, name, digest, created_at))
                db.commit()
                self.respond(201, {'id': inbox_id, 'name': name, 'created_at': created_at, 'token': token})
                return

            if is_inboxes:
                rows = db.execute('SELECT id, name, created_at FROM inboxes ORDER BY created_at DESC, id DESC')
                self.respond(200, {'inboxes': [dict(row) for row in rows]})
                return

            if is_hook:
                inbox_id, token = parts[2], parts[3]
                inbox = db.execute('SELECT token_sha256 FROM inboxes WHERE id = ?', (inbox_id,)).fetchone()
                digest = hashlib.sha256(token.encode('utf-8')).hexdigest()
                stored = inbox['token_sha256'] if inbox else '0' * 64
                matches = hmac.compare_digest(digest, stored)
                if inbox is None or not matches:
                    raise Rejection(404, 'Not found')
                captured = {}
                for header in ('content-type', 'x-webhook-id', 'x-event-type'):
                    value = self.headers.get(header)
                    if value is not None:
                        captured[header] = value
                external_id = captured.get('x-webhook-id') or None
                body_hash = hashlib.sha256(raw).hexdigest()
                db.execute('BEGIN IMMEDIATE')
                if external_id is not None:
                    previous = db.execute('SELECT id, body FROM events WHERE inbox_id = ? AND external_id = ?',
                                          (inbox_id, external_id)).fetchone()
                    if previous is not None:
                        if previous['body'] != body:
                            db.rollback()
                            raise Rejection(409, 'Idempotency key already used with a different body')
                        db.commit()
                        self.respond(200, {'id': previous['id'], 'duplicate': True})
                        return
                event_id = secrets.token_urlsafe(24)
                db.execute('INSERT INTO events (id, inbox_id, external_id, received_at, headers_json, body, body_sha256) VALUES (?, ?, ?, ?, ?, ?, ?)',
                           (event_id, inbox_id, external_id, timestamp(), json.dumps(captured), body, body_hash))
                db.commit()
                self.respond(201, {'id': event_id, 'duplicate': False})
                return

            if is_event_list:
                try:
                    query = parse_qs(url.query, keep_blank_values=True)
                except ValueError:
                    raise Rejection(400, 'Invalid query')
                values = query.get('limit', ['20'])
                if len(values) != 1 or re.fullmatch(r'[0-9]+', values[0]) is None:
                    raise Rejection(400, 'Invalid limit')
                digits = values[0].lstrip('0') or '0'
                if len(digits) > 3 or not 1 <= int(digits) <= 100:
                    raise Rejection(400, 'Invalid limit')
                limit = int(digits)
                inbox_id = parts[3]
                if db.execute('SELECT id FROM inboxes WHERE id = ?', (inbox_id,)).fetchone() is None:
                    raise Rejection(404, 'Not found')
                rows = db.execute('SELECT ' + SUMMARY + ' FROM events WHERE inbox_id = ? ORDER BY received_at DESC, id DESC LIMIT ?',
                                  (inbox_id, limit))
                self.respond(200, {'events': [dict(row) for row in rows]})
                return

            row = db.execute('SELECT ' + SUMMARY + ', body, headers_json FROM events WHERE id = ?',
                             (parts[3],)).fetchone()
            if row is None:
                raise Rejection(404, 'Not found')
            event = dict(row)
            event['headers'] = json.loads(event.pop('headers_json'))
            self.respond(200, event)
        except Rejection as exc:
            self.respond(exc.status, {'error': exc.message})
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception:
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
