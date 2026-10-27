import os
import sys
import json
import re
import hmac
import hashlib
import secrets
import sqlite3
import socket
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit, unquote, parse_qs


MAX_BODY = 65536
DB_PATH = os.environ.get('PHOENIX_DB_PATH', 'phoenix.sqlite')
ADMIN_TOKEN = os.environ.get('PHOENIX_ADMIN_TOKEN', '')


def connect_db():
    conn = sqlite3.connect(DB_PATH, timeout=15)
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA foreign_keys=ON')
    conn.execute('PRAGMA busy_timeout=15000')
    return conn


def timestamp():
    return datetime.now(timezone.utc).isoformat(timespec='microseconds').replace('+00:00', 'Z')


def digest(data):
    return hashlib.sha256(data).hexdigest()


def summary(row):
    return {key: row[key] for key in (
        'id', 'inbox_id', 'external_id', 'received_at', 'body_sha256'
    )}


class Handler(BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.0'
    server_version = 'Phoenix'
    sys_version = ''

    def setup(self):
        super().setup()
        self.connection.settimeout(15)

    def log_message(self, format, *args):
        pass

    def reply(self, status, payload):
        data = json.dumps(payload, ensure_ascii=True, separators=(',', ':')).encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(data)))
        self.send_header('Connection', 'close')
        self.end_headers()
        self.close_connection = True
        if self.command != 'HEAD':
            self.wfile.write(data)

    def error(self, status, message):
        self.close_connection = True
        self.reply(status, {'error': message})

    def send_error(self, code, message=None, explain=None):
        if code == 501:
            code = 405
        messages = {
            400: 'Bad request', 401: 'Unauthorized', 404: 'Not found',
            405: 'Method not allowed', 408: 'Request timeout',
            413: 'Body too large', 414: 'Request URI too long',
            431: 'Request headers too large', 500: 'Internal server error',
            505: 'HTTP version not supported'
        }
        self.error(code, messages.get(code, 'Request rejected'))

    def __getattr__(self, name):
        if name.startswith('do_'):
            return self.dispatch
        raise AttributeError(name)

    def read_body(self):
        if self.headers.get_all('Transfer-Encoding') is not None:
            self.error(400, 'Transfer-Encoding is not supported')
            return None
        lengths = self.headers.get_all('Content-Length')
        if lengths is None:
            length = 0
        elif len(lengths) != 1 or re.fullmatch(r'[0-9]+', lengths[0].strip()) is None:
            self.error(400, 'Invalid Content-Length')
            return None
        else:
            value = lengths[0].strip().lstrip('0') or '0'
            if len(value) > 5:
                self.error(413, 'Body too large')
                return None
            length = int(value)
        if length > MAX_BODY:
            self.error(413, 'Body too large')
            return None
        try:
            raw = self.rfile.read(length)
        except (socket.timeout, OSError):
            self.error(400, 'Incomplete request body')
            return None
        if len(raw) != length:
            self.error(400, 'Incomplete request body')
            return None
        try:
            body = raw.decode('utf-8', errors='strict')
        except UnicodeDecodeError:
            self.error(400, 'Body must be valid UTF-8')
            return None
        return raw, body

    def dispatch(self):
        conn = None
        try:
            try:
                parsed = urlsplit(self.path)
                segments = [unquote(p, encoding='utf-8', errors='strict')
                            for p in parsed.path.split('/')]
            except (ValueError, UnicodeError):
                self.error(400, 'Invalid request URL')
                return

            if len(segments) > 1 and segments[1] == 'api':
                credentials = self.headers.get_all('Authorization') or []
                expected = ('Bearer ' + ADMIN_TOKEN).encode('utf-8')
                supplied = credentials[0].encode('utf-8') if len(credentials) == 1 else b''
                if not hmac.compare_digest(supplied, expected):
                    self.error(401, 'Unauthorized')
                    return

            body_result = self.read_body()
            if body_result is None:
                return
            raw, body = body_result

            route = None
            allowed = ()
            if segments == ['', 'health']:
                route, allowed = 'health', ('GET',)
            elif segments == ['', 'api', 'inboxes']:
                route, allowed = 'inboxes', ('GET', 'POST')
            elif (len(segments) == 5 and segments[:3] == ['', 'api', 'inboxes']
                  and segments[3] and segments[4] == 'events'):
                route, allowed = 'events', ('GET',)
            elif (len(segments) == 4 and segments[:3] == ['', 'api', 'events']
                  and segments[3]):
                route, allowed = 'event', ('GET',)
            elif (len(segments) == 4 and segments[:2] == ['', 'hooks']
                  and segments[2] and segments[3]):
                route, allowed = 'hook', ('POST',)

            if self.command not in ('GET', 'POST'):
                self.error(405, 'Method not allowed')
                return
            if route is None:
                self.error(404, 'Not found')
                return
            if self.command not in allowed:
                self.error(405, 'Method not allowed')
                return
            if route == 'health':
                self.reply(200, {'status': 'ok', 'schema_version': 1})
                return

            conn = connect_db()
            if route == 'inboxes':
                if self.command == 'GET':
                    rows = conn.execute(
                        'SELECT id, name, created_at FROM inboxes ORDER BY created_at DESC, id DESC'
                    ).fetchall()
                    self.reply(200, {'inboxes': [dict(row) for row in rows]})
                    return
                try:
                    value = json.loads(body)
                except (ValueError, RecursionError):
                    self.error(400, 'Invalid JSON')
                    return
                if not isinstance(value, dict) or not isinstance(value.get('name'), str):
                    self.error(400, 'A name string is required')
                    return
                name = value['name'].strip()
                if not 1 <= len(name) <= 80:
                    self.error(400, 'Name must contain 1 to 80 characters')
                    return
                try:
                    name.encode('utf-8')
                except UnicodeError:
                    self.error(400, 'Invalid name')
                    return
                inbox_id = secrets.token_urlsafe(24)
                token = secrets.token_urlsafe(32)
                created = timestamp()
                conn.execute(
                    'INSERT INTO inboxes (id, name, token_sha256, created_at) VALUES (?, ?, ?, ?)',
                    (inbox_id, name, digest(token.encode('utf-8')), created)
                )
                conn.commit()
                self.reply(201, {'id': inbox_id, 'name': name, 'created_at': created, 'token': token})
                return

            if route == 'events':
                try:
                    query = parse_qs(parsed.query, keep_blank_values=True, encoding='utf-8', errors='strict')
                    limits = query.get('limit', ['20'])
                    if len(limits) != 1 or re.fullmatch(r'[0-9]+', limits[0]) is None:
                        raise ValueError()
                    value = limits[0].lstrip('0') or '0'
                    if len(value) > 3:
                        raise ValueError()
                    limit = int(value)
                    if not 1 <= limit <= 100:
                        raise ValueError()
                except (ValueError, UnicodeError):
                    self.error(400, 'Invalid limit')
                    return
                if conn.execute('SELECT id FROM inboxes WHERE id=?', (segments[3],)).fetchone() is None:
                    self.error(404, 'Not found')
                    return
                rows = conn.execute(
                    'SELECT id, inbox_id, external_id, received_at, body_sha256 FROM events '
                    'WHERE inbox_id=? ORDER BY received_at DESC, id DESC LIMIT ?',
                    (segments[3], limit)
                ).fetchall()
                self.reply(200, {'events': [summary(row) for row in rows]})
                return

            if route == 'event':
                row = conn.execute('SELECT * FROM events WHERE id=?', (segments[3],)).fetchone()
                if row is None:
                    self.error(404, 'Not found')
                    return
                result = summary(row)
                result['body'] = row['body']
                result['headers'] = json.loads(row['headers_json'])
                self.reply(200, result)
                return

            if route == 'hook':
                inbox_id, token = segments[2], segments[3]
                conn.execute('BEGIN IMMEDIATE')
                inbox = conn.execute('SELECT token_sha256 FROM inboxes WHERE id=?', (inbox_id,)).fetchone()
                token_hash = digest(token.encode('utf-8'))
                if inbox is None or not hmac.compare_digest(inbox['token_sha256'].encode('utf-8'), token_hash.encode('ascii')):
                    conn.rollback()
                    self.error(404, 'Not found')
                    return
                external_id = self.headers.get('X-Webhook-Id') or None
                body_hash = digest(raw)
                if external_id is not None:
                    previous = conn.execute(
                        'SELECT id, body FROM events WHERE inbox_id=? AND external_id=?',
                        (inbox_id, external_id)
                    ).fetchone()
                    if previous is not None:
                        conn.rollback()
                        if previous['body'] == body:
                            self.reply(200, {'id': previous['id'], 'duplicate': True})
                        else:
                            self.error(409, 'Idempotency key already used with a different body')
                        return
                captured = {}
                for header in ('content-type', 'x-webhook-id', 'x-event-type'):
                    value = self.headers.get(header)
                    if value is not None:
                        captured[header] = value
                event_id = secrets.token_urlsafe(24)
                conn.execute(
                    'INSERT INTO events '
                    '(id, inbox_id, external_id, received_at, headers_json, body, body_sha256) '
                    'VALUES (?, ?, ?, ?, ?, ?, ?)',
                    (event_id, inbox_id, external_id, timestamp(),
                     json.dumps(captured, ensure_ascii=True, separators=(',', ':')), body, body_hash)
                )
                conn.commit()
                self.reply(201, {'id': event_id, 'duplicate': False})
        except (BrokenPipeError, ConnectionResetError, socket.timeout):
            self.close_connection = True
        except Exception:
            if conn is not None:
                try:
                    conn.rollback()
                except Exception:
                    pass
            try:
                self.error(500, 'Internal server error')
            except Exception:
                self.close_connection = True
        finally:
            if conn is not None:
                conn.close()

    do_GET = dispatch
    do_POST = dispatch
    do_HEAD = dispatch
    do_PUT = dispatch
    do_PATCH = dispatch
    do_DELETE = dispatch
    do_OPTIONS = dispatch
    do_TRACE = dispatch
    do_CONNECT = dispatch


class Server(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def handle_error(self, request, client_address):
        pass


def main():
    if not ADMIN_TOKEN:
        sys.stderr.write('PHOENIX_ADMIN_TOKEN is required\n')
        return 1
    try:
        port = int(os.environ.get('PORT', '8787'))
        if not 0 <= port <= 65535:
            raise ValueError()
    except ValueError:
        sys.stderr.write('Invalid PORT\n')
        return 1
    try:
        conn = connect_db()
        try:
            conn.executescript('''
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
''')
            conn.commit()
        finally:
            conn.close()
        server = Server(('127.0.0.1', port), Handler)
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
