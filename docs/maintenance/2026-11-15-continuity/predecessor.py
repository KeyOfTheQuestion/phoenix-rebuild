import os
import json
import sqlite3
import hashlib
import secrets
import hmac
from datetime import datetime, timezone, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit, parse_qs

DB_PATH = os.environ.get('PHOENIX_DB_PATH', 'phoenix.sqlite')
ADMIN_TOKEN = os.environ.get('PHOENIX_ADMIN_TOKEN', '')
MAX_BODY = 65536


def connect():
    connection = sqlite3.connect(DB_PATH, timeout=30)
    connection.row_factory = sqlite3.Row
    connection.execute('PRAGMA foreign_keys=ON')
    connection.execute('PRAGMA busy_timeout=30000')
    return connection


def initialize():
    connection = connect()
    try:
        connection.execute('BEGIN IMMEDIATE')
        version = connection.execute('PRAGMA user_version').fetchone()[0]
        if version not in (0, 1, 2):
            raise RuntimeError('Unsupported database version')
        if version in (0, 1):
            connection.execute('''CREATE TABLE IF NOT EXISTS inboxes (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                token_sha256 TEXT NOT NULL,
                created_at TEXT NOT NULL
            )''')
            connection.execute('''CREATE TABLE IF NOT EXISTS events (
                id TEXT PRIMARY KEY,
                inbox_id TEXT NOT NULL REFERENCES inboxes(id),
                external_id TEXT,
                received_at TEXT NOT NULL,
                headers_json TEXT NOT NULL,
                body TEXT NOT NULL,
                body_sha256 TEXT NOT NULL,
                UNIQUE(inbox_id, external_id)
            )''')
            connection.execute('''CREATE TABLE IF NOT EXISTS retention_policies (
                inbox_id TEXT PRIMARY KEY REFERENCES inboxes(id),
                max_age_seconds INTEGER NOT NULL CHECK(max_age_seconds BETWEEN 1 AND 2592000)
            )''')
            connection.execute('PRAGMA user_version=2')
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def now_text():
    return datetime.now(timezone.utc).isoformat()


def instant(value):
    if value.endswith('Z'):
        value = value[:-1] + '+00:00'
    result = datetime.fromisoformat(value)
    if result.tzinfo is None:
        result = result.replace(tzinfo=timezone.utc)
    return result.astimezone(timezone.utc)


class Problem(Exception):
    def __init__(self, status, message):
        self.status = status
        self.message = message


class Handler(BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'

    def log_message(self, format, *args):
        pass

    def __getattr__(self, name):
        if name.startswith('do_'):
            return self.handle_request
        raise AttributeError(name)

    def send_json(self, status, value):
        data = json.dumps(value, ensure_ascii=True, separators=(',', ':')).encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(data)))
        if self.close_connection:
            self.send_header('Connection', 'close')
        self.end_headers()
        if self.command != 'HEAD':
            self.wfile.write(data)

    def send_error(self, code, message=None, explain=None):
        self.close_connection = True
        self.send_json(code, {'error': 'Invalid HTTP request'})

    def handle_expect_100(self):
        return True

    def read_body(self):
        if self.headers.get_all('Transfer-Encoding') is not None:
            raise Problem(400, 'Transfer-Encoding is unsupported')
        lengths = self.headers.get_all('Content-Length', [])
        if not lengths:
            if self.command == 'POST':
                return ''
            if self.command in ('GET', 'HEAD', 'OPTIONS', 'DELETE'):
                return ''
            raise Problem(400, 'Content-Length is required')
        if len(lengths) != 1:
            raise Problem(400, 'Invalid Content-Length')
        value = lengths[0].strip()
        if not value or any(character not in '0123456789' for character in value):
            raise Problem(400, 'Invalid Content-Length')
        if len(value.lstrip('0')) > 6:
            raise Problem(413, 'Body too large')
        length = int(value)
        if length > MAX_BODY:
            raise Problem(413, 'Body too large')
        if self.headers.get('Expect', '').lower() == '100-continue':
            self.send_response_only(100)
            self.end_headers()
        raw = self.rfile.read(length)
        if len(raw) != length:
            raise Problem(400, 'Incomplete body')
        try:
            return raw.decode('utf-8')
        except UnicodeDecodeError:
            raise Problem(400, 'Invalid UTF-8')

    def parse_json(self, body):
        def invalid_constant(value):
            raise ValueError('Invalid JSON constant')
        try:
            return json.loads(body, parse_constant=invalid_constant)
        except (ValueError, RecursionError):
            raise Problem(400, 'Invalid JSON')

    def require_method(self, *methods):
        if self.command not in methods:
            raise Problem(405, 'Method not allowed')

    def require_inbox(self, connection, inbox_id):
        row = connection.execute('SELECT id FROM inboxes WHERE id=?', (inbox_id,)).fetchone()
        if row is None:
            raise Problem(404, 'Inbox not found')

    def handle_request(self):
        connection = None
        try:
            parsed = urlsplit(self.path)
            path = parsed.path
            if path.startswith('/api/'):
                credentials = self.headers.get_all('Authorization', [])
                expected = ('Bearer ' + ADMIN_TOKEN).encode('utf-8')
                if len(credentials) != 1 or not hmac.compare_digest(credentials[0].encode('utf-8'), expected):
                    raise Problem(401, 'Unauthorized')
            body = self.read_body()
            connection = connect()
            status, result = self.dispatch(connection, path, parsed.query, body)
            self.send_json(status, result)
        except Problem as error:
            self.close_connection = True
            self.send_json(error.status, {'error': error.message})
        except (BrokenPipeError, ConnectionResetError):
            self.close_connection = True
        except Exception:
            self.close_connection = True
            self.send_json(500, {'error': 'Internal server error'})
        finally:
            if connection is not None:
                connection.close()

    def dispatch(self, connection, path, query, body):
        parts = path.split('/')
        if path == '/health':
            self.require_method('GET')
            return 200, {'status': 'ok', 'schema_version': 2}
        if path == '/api/inboxes':
            self.require_method('GET', 'POST')
            if self.command == 'GET':
                rows = connection.execute('SELECT id,name,created_at FROM inboxes ORDER BY created_at DESC,id DESC').fetchall()
                return 200, {'inboxes': [dict(row) for row in rows]}
            data = self.parse_json(body)
            if not isinstance(data, dict) or not isinstance(data.get('name'), str):
                raise Problem(400, 'Invalid name')
            name = data['name'].strip()
            if not 1 <= len(name) <= 80:
                raise Problem(400, 'Invalid name')
            inbox_id = secrets.token_urlsafe(24)
            token = secrets.token_urlsafe(32)
            created = now_text()
            connection.execute('INSERT INTO inboxes (id,name,token_sha256,created_at) VALUES (?,?,?,?)',
                               (inbox_id, name, hashlib.sha256(token.encode('utf-8')).hexdigest(), created))
            connection.commit()
            return 201, {'id': inbox_id, 'name': name, 'created_at': created, 'token': token}
        if len(parts) == 4 and parts[1] == 'hooks' and parts[2] and parts[3]:
            self.require_method('POST')
            inbox_id, token = parts[2:4]
            row = connection.execute('SELECT token_sha256 FROM inboxes WHERE id=?', (inbox_id,)).fetchone()
            digest = hashlib.sha256(token.encode('utf-8')).hexdigest()
            if row is None or not hmac.compare_digest(row['token_sha256'], digest):
                raise Problem(404, 'Not found')
            headers = {}
            for key in ('content-type', 'x-webhook-id', 'x-event-type'):
                value = self.headers.get(key)
                if value is not None:
                    headers[key] = value
            external_id = self.headers.get('X-Webhook-Id') or None
            body_digest = hashlib.sha256(body.encode('utf-8')).hexdigest()
            connection.execute('BEGIN IMMEDIATE')
            if external_id is not None:
                existing = connection.execute('SELECT id,body FROM events WHERE inbox_id=? AND external_id=?', (inbox_id, external_id)).fetchone()
                if existing is not None:
                    if existing['body'] != body:
                        raise Problem(409, 'Idempotency key conflicts with stored body')
                    connection.commit()
                    return 200, {'id': existing['id'], 'duplicate': True}
            event_id = secrets.token_urlsafe(24)
            connection.execute('INSERT INTO events (id,inbox_id,external_id,received_at,headers_json,body,body_sha256) VALUES (?,?,?,?,?,?,?)',
                               (event_id, inbox_id, external_id, now_text(), json.dumps(headers), body, body_digest))
            connection.commit()
            return 201, {'id': event_id, 'duplicate': False}
        if len(parts) == 5 and parts[1:3] == ['api', 'inboxes'] and parts[3]:
            inbox_id, action = parts[3:5]
            if action == 'events':
                self.require_method('GET')
                parameters = parse_qs(query, keep_blank_values=True)
                limits = parameters.get('limit', ['20'])
                if len(limits) != 1 or not limits[0] or any(char not in '0123456789' for char in limits[0]):
                    raise Problem(400, 'Invalid limit')
                value = limits[0].lstrip('0')
                if len(value) > 3:
                    raise Problem(400, 'Invalid limit')
                limit = int(value or '0')
                if not 1 <= limit <= 100:
                    raise Problem(400, 'Invalid limit')
                self.require_inbox(connection, inbox_id)
                rows = connection.execute('SELECT id,inbox_id,external_id,received_at,body_sha256 FROM events WHERE inbox_id=? ORDER BY received_at DESC,id DESC LIMIT ?', (inbox_id, limit)).fetchall()
                return 200, {'events': [dict(row) for row in rows]}
            if action == 'retention':
                self.require_method('GET', 'PUT')
                if self.command == 'GET':
                    self.require_inbox(connection, inbox_id)
                    policy = connection.execute('SELECT max_age_seconds FROM retention_policies WHERE inbox_id=?', (inbox_id,)).fetchone()
                    return 200, {'max_age_seconds': policy[0] if policy is not None else None}
                data = self.parse_json(body)
                if not isinstance(data, dict) or set(data) != {'max_age_seconds'}:
                    raise Problem(400, 'Invalid retention policy')
                age = data['max_age_seconds']
                if age is not None and (type(age) is not int or not 1 <= age <= 2592000):
                    raise Problem(400, 'Invalid retention policy')
                connection.execute('BEGIN IMMEDIATE')
                self.require_inbox(connection, inbox_id)
                if age is None:
                    connection.execute('DELETE FROM retention_policies WHERE inbox_id=?', (inbox_id,))
                else:
                    connection.execute('INSERT INTO retention_policies (inbox_id,max_age_seconds) VALUES (?,?) ON CONFLICT(inbox_id) DO UPDATE SET max_age_seconds=excluded.max_age_seconds', (inbox_id, age))
                connection.commit()
                return 200, {'max_age_seconds': age}
            if action == 'expire':
                self.require_method('POST')
                data = {} if body == '' else self.parse_json(body)
                if not isinstance(data, dict) or not set(data).issubset({'limit'}):
                    raise Problem(400, 'Invalid expiration request')
                limit = data.get('limit', 100)
                if type(limit) is not int or not 1 <= limit <= 100:
                    raise Problem(400, 'Invalid limit')
                connection.execute('BEGIN IMMEDIATE')
                self.require_inbox(connection, inbox_id)
                policy = connection.execute('SELECT max_age_seconds FROM retention_policies WHERE inbox_id=?', (inbox_id,)).fetchone()
                current = datetime.now(timezone.utc)
                deleted = 0
                if policy is not None:
                    cutoff = current - timedelta(seconds=policy[0])
                    eligible = []
                    rows = connection.execute('SELECT id,received_at FROM events WHERE inbox_id=?', (inbox_id,)).fetchall()
                    for row in rows:
                        received = instant(row['received_at'])
                        if received <= cutoff:
                            eligible.append((received, row['id']))
                    eligible.sort()
                    for received, event_id in eligible[:limit]:
                        cursor = connection.execute('DELETE FROM events WHERE id=? AND inbox_id=?', (event_id, inbox_id))
                        deleted += cursor.rowcount
                connection.commit()
                return 200, {'deleted': deleted}
        if len(parts) == 4 and parts[1:3] == ['api', 'events'] and parts[3]:
            self.require_method('GET')
            row = connection.execute('SELECT id,inbox_id,external_id,received_at,body_sha256,body,headers_json FROM events WHERE id=?', (parts[3],)).fetchone()
            if row is None:
                raise Problem(404, 'Event not found')
            result = dict(row)
            result['headers'] = json.loads(result.pop('headers_json'))
            return 200, result
        if self.command not in ('GET', 'POST', 'PUT'):
            raise Problem(405, 'Method not allowed')
        raise Problem(404, 'Not found')


class Server(ThreadingHTTPServer):
    daemon_threads = True

    def handle_error(self, request, client_address):
        pass


def main():
    if not ADMIN_TOKEN:
        raise SystemExit('PHOENIX_ADMIN_TOKEN is required')
    try:
        initialize()
        server = Server(('127.0.0.1', int(os.environ.get('PORT', '8787'))), Handler)
    except Exception:
        raise SystemExit('Service initialization failed')
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == '__main__':
    main()
