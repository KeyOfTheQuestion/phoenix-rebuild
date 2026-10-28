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
from urllib.parse import urlsplit, parse_qs


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


def connect(path):
    db = sqlite3.connect(path, timeout=30)
    db.row_factory = sqlite3.Row
    db.execute('PRAGMA foreign_keys=ON')
    db.execute('PRAGMA busy_timeout=30000')
    return db


class Handler(BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'

    def log_message(self, format, *args):
        pass

    def respond(self, status, value):
        data = json.dumps(value, ensure_ascii=True, separators=(',', ':')).encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(data)))
        if self.close_connection:
            self.send_header('Connection', 'close')
        self.end_headers()
        if self.command != 'HEAD':
            self.wfile.write(data)

    def error(self, status, message, close=False):
        if close:
            self.close_connection = True
        self.respond(status, {'error': message})

    def send_error(self, code, message=None, explain=None):
        self.error(code, {400: 'Bad request', 404: 'Not found', 405: 'Method not allowed', 414: 'Request URI too long', 431: 'Request headers too large', 501: 'Method not allowed'}.get(code, 'Request failed'), True)

    def handle_expect_100(self):
        # Read the request only after normal framing and authentication checks.
        return True

    def __getattr__(self, name):
        if name.startswith('do_'):
            return self.dispatch
        raise AttributeError(name)

    def dispatch(self):
        db = None
        try:
            self.route()
        except (BrokenPipeError, ConnectionResetError, TimeoutError):
            self.close_connection = True
        except Exception:
            self.error(500, 'Internal server error', True)

    def route(self):
        try:
            parsed = urlsplit(self.path)
            path = parsed.path
        except ValueError:
            self.error(400, 'Invalid request target', True)
            return

        if path.startswith('/api/'):
            auth = self.headers.get_all('Authorization', [])
            expected = ('Bearer ' + self.server.admin_token).encode('utf-8')
            if len(auth) != 1 or not hmac.compare_digest(auth[0].encode('utf-8'), expected):
                self.error(401, 'Unauthorized', True)
                return

        if self.headers.get_all('Transfer-Encoding') is not None:
            self.error(400, 'Transfer-Encoding is not supported', True)
            return
        lengths = self.headers.get_all('Content-Length', [])
        if len(lengths) > 1 or (lengths and not re.fullmatch(r'[0-9]+', lengths[0].strip())):
            self.error(400, 'Invalid Content-Length', True)
            return
        if lengths:
            length_text = lengths[0].strip().lstrip('0') or '0'
            if len(length_text) > 5 or int(length_text) > 65536:
                self.error(413, 'Body too large', True)
                return
            length = int(length_text)
        else:
            length = 0
        if self.headers.get('Expect', '').lower() == '100-continue':
            self.send_response_only(100)
            self.end_headers()
        raw = self.rfile.read(length) if length else b''
        if len(raw) != length:
            self.error(400, 'Incomplete body', True)
            return
        try:
            body = raw.decode('utf-8')
        except UnicodeDecodeError:
            self.error(400, 'Invalid UTF-8', True)
            return

        hook = re.fullmatch(r'/hooks/([^/]+)/([^/]+)', path)
        listing = re.fullmatch(r'/api/inboxes/([^/]+)/events', path)
        detail = re.fullmatch(r'/api/events/([^/]+)', path)
        allowed = None
        if path == '/health':
            allowed = ('GET',)
        elif path == '/api/inboxes':
            allowed = ('GET', 'POST')
        elif hook:
            allowed = ('POST',)
        elif listing or detail:
            allowed = ('GET',)
        if self.command not in ('GET', 'POST') or (allowed is not None and self.command not in allowed):
            self.error(405, 'Method not allowed', True)
            return
        if allowed is None:
            self.error(404, 'Not found')
            return
        if path == '/health':
            self.respond(200, {'status': 'ok', 'schema_version': 1})
            return

        db = connect(self.server.db_path)
        try:
            if path == '/api/inboxes':
                if self.command == 'GET':
                    rows = db.execute('SELECT id, name, created_at FROM inboxes ORDER BY created_at DESC, id DESC').fetchall()
                    self.respond(200, {'inboxes': [dict(row) for row in rows]})
                    return
                try:
                    value = json.loads(body)
                except (ValueError, RecursionError):
                    self.error(400, 'Invalid JSON', True)
                    return
                name = value.get('name') if isinstance(value, dict) else None
                if not isinstance(name, str) or not 1 <= len(name.strip()) <= 80:
                    self.error(400, 'Invalid name', True)
                    return
                name = name.strip()
                try:
                    name.encode('utf-8')
                except UnicodeEncodeError:
                    self.error(400, 'Invalid name', True)
                    return
                inbox_id = secrets.token_hex(16)
                token = secrets.token_urlsafe(32)
                created_at = now()
                db.execute('INSERT INTO inboxes (id, name, token_sha256, created_at) VALUES (?, ?, ?, ?)', (inbox_id, name, hashlib.sha256(token.encode('utf-8')).hexdigest(), created_at))
                db.commit()
                self.respond(201, {'id': inbox_id, 'name': name, 'created_at': created_at, 'token': token})
                return

            if hook:
                inbox_id, token = hook.groups()
                db.execute('BEGIN IMMEDIATE')
                inbox = db.execute('SELECT token_sha256 FROM inboxes WHERE id=?', (inbox_id,)).fetchone()
                digest = hashlib.sha256(token.encode('utf-8')).hexdigest()
                if inbox is None or not hmac.compare_digest(digest, inbox['token_sha256']):
                    db.rollback()
                    self.error(404, 'Not found', True)
                    return
                headers = {}
                for key in ('content-type', 'x-webhook-id', 'x-event-type'):
                    if key in self.headers:
                        headers[key] = self.headers.get(key)
                external_id = headers.get('x-webhook-id') or None
                body_hash = hashlib.sha256(raw).hexdigest()
                if external_id is not None:
                    prior = db.execute('SELECT id, body FROM events WHERE inbox_id=? AND external_id=?', (inbox_id, external_id)).fetchone()
                    if prior is not None:
                        db.rollback()
                        if prior['body'] != body:
                            self.error(409, 'Idempotency key already used with a different body', True)
                        else:
                            self.respond(200, {'id': prior['id'], 'duplicate': True})
                        return
                event_id = secrets.token_hex(16)
                db.execute('INSERT INTO events (id, inbox_id, external_id, received_at, headers_json, body, body_sha256) VALUES (?, ?, ?, ?, ?, ?, ?)', (event_id, inbox_id, external_id, now(), json.dumps(headers, ensure_ascii=True), body, body_hash))
                db.commit()
                self.respond(201, {'id': event_id, 'duplicate': False})
                return

            if listing:
                try:
                    query = parse_qs(parsed.query, keep_blank_values=True)
                    limits = query.get('limit', ['20'])
                    if len(limits) != 1 or not re.fullmatch(r'[0-9]+', limits[0]):
                        raise ValueError()
                    normalized = limits[0].lstrip('0') or '0'
                    if len(normalized) > 3:
                        raise ValueError()
                    limit = int(normalized)
                    if not 1 <= limit <= 100:
                        raise ValueError()
                except ValueError:
                    self.error(400, 'Invalid limit')
                    return
                inbox_id = listing.group(1)
                if db.execute('SELECT 1 FROM inboxes WHERE id=?', (inbox_id,)).fetchone() is None:
                    self.error(404, 'Not found')
                    return
                rows = db.execute('SELECT ' + SUMMARY + ' FROM events WHERE inbox_id=? ORDER BY received_at DESC, id DESC LIMIT ?', (inbox_id, limit)).fetchall()
                self.respond(200, {'events': [dict(row) for row in rows]})
                return

            row = db.execute('SELECT ' + SUMMARY + ', body, headers_json FROM events WHERE id=?', (detail.group(1),)).fetchone()
            if row is None:
                self.error(404, 'Not found')
                return
            result = dict(row)
            result['headers'] = json.loads(result.pop('headers_json'))
            self.respond(200, result)
        finally:
            db.close()

    do_GET = dispatch
    do_POST = dispatch
    do_HEAD = dispatch
    do_PUT = dispatch
    do_DELETE = dispatch
    do_PATCH = dispatch
    do_OPTIONS = dispatch


class Server(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True


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
        server.db_path = path
        server.admin_token = token
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
