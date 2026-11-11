import os
import json
import sqlite3
import secrets
import hashlib
import hmac
import re
from datetime import datetime, timezone, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit, parse_qs, unquote

DB_PATH = os.environ.get('PHOENIX_DB_PATH', 'phoenix.sqlite')
ADMIN_TOKEN = os.environ.get('PHOENIX_ADMIN_TOKEN', '')
MAX_BODY = 65536


def connect():
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA foreign_keys=ON')
    conn.execute('PRAGMA busy_timeout=30000')
    return conn


def initialize():
    conn = connect()
    try:
        conn.execute('BEGIN IMMEDIATE')
        version = conn.execute('PRAGMA user_version').fetchone()[0]
        if version not in (0, 1, 2):
            raise RuntimeError('Unsupported database schema version')
        if version in (0, 1):
            conn.execute('''CREATE TABLE IF NOT EXISTS inboxes (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                token_sha256 TEXT NOT NULL,
                created_at TEXT NOT NULL
            )''')
            conn.execute('''CREATE TABLE IF NOT EXISTS events (
                id TEXT PRIMARY KEY,
                inbox_id TEXT NOT NULL REFERENCES inboxes(id),
                external_id TEXT,
                received_at TEXT NOT NULL,
                headers_json TEXT NOT NULL,
                body TEXT NOT NULL,
                body_sha256 TEXT NOT NULL,
                UNIQUE(inbox_id, external_id)
            )''')
            conn.execute('''CREATE TABLE IF NOT EXISTS retention_policies (
                inbox_id TEXT PRIMARY KEY REFERENCES inboxes(id),
                max_age_seconds INTEGER NOT NULL
                    CHECK(max_age_seconds BETWEEN 1 AND 2592000)
            )''')
            conn.execute('PRAGMA user_version=2')
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def now_text():
    return datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')


def instant(value):
    if value.endswith('Z'):
        value = value[:-1] + '+00:00'
    result = datetime.fromisoformat(value)
    if result.tzinfo is None:
        result = result.replace(tzinfo=timezone.utc)
    return result.astimezone(timezone.utc)


class HTTPError(Exception):
    def __init__(self, status, message):
        self.status = status
        self.message = message


class Handler(BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'

    def log_message(self, format, *args):
        pass

    def __getattr__(self, name):
        if name.startswith('do_'):
            return self.dispatch
        raise AttributeError(name)

    def send_json(self, status, payload):
        data = json.dumps(payload, ensure_ascii=True, separators=(',', ':')).encode('utf-8')
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

    def read_body(self):
        if self.headers.get_all('Transfer-Encoding') is not None:
            raise HTTPError(400, 'Transfer-Encoding is not supported')
        lengths = self.headers.get_all('Content-Length') or []
        if not lengths:
            return ''
        if len(lengths) != 1 or re.fullmatch(r'[0-9]+', lengths[0].strip()) is None:
            raise HTTPError(400, 'Invalid Content-Length')
        value = lengths[0].strip()
        if len(value.lstrip('0')) > 5:
            raise HTTPError(413, 'Body too large')
        size = int(value)
        if size > MAX_BODY:
            raise HTTPError(413, 'Body too large')
        raw = self.rfile.read(size)
        if len(raw) != size:
            raise HTTPError(400, 'Incomplete request body')
        try:
            return raw.decode('utf-8')
        except UnicodeDecodeError:
            raise HTTPError(400, 'Body must be valid UTF-8')

    def object_body(self, body):
        def invalid_constant(value):
            raise ValueError('Invalid JSON constant')
        try:
            value = json.loads(body, parse_constant=invalid_constant)
        except (ValueError, RecursionError):
            raise HTTPError(400, 'Invalid JSON')
        if not isinstance(value, dict):
            raise HTTPError(400, 'Expected a JSON object')
        return value

    def require_method(self, *allowed):
        if self.command not in allowed:
            raise HTTPError(405, 'Method not allowed')

    def inbox(self, conn, inbox_id):
        row = conn.execute('SELECT id FROM inboxes WHERE id=?', (inbox_id,)).fetchone()
        if row is None:
            raise HTTPError(404, 'Inbox not found')

    def summary(self, row):
        return {key: row[key] for key in (
            'id', 'inbox_id', 'external_id', 'received_at', 'body_sha256'
        )}

    def dispatch(self):
        conn = None
        try:
            parsed = urlsplit(self.path)
            path = parsed.path
            if path.startswith('/api/'):
                credentials = self.headers.get_all('Authorization') or []
                expected = ('Bearer ' + ADMIN_TOKEN).encode('utf-8')
                if len(credentials) != 1 or not hmac.compare_digest(
                    credentials[0].encode('utf-8'), expected
                ):
                    raise HTTPError(401, 'Unauthorized')
            body = self.read_body()
            segments = [unquote(part) for part in path.split('/')]
            if path == '/health':
                self.require_method('GET')
                self.send_json(200, {'status': 'ok', 'schema_version': 2})
                return
            conn = connect()
            status, result = self.route(conn, path, segments, parsed.query, body)
            self.send_json(status, result)
        except HTTPError as exc:
            if conn is not None:
                conn.rollback()
            self.close_connection = True
            self.send_json(exc.status, {'error': exc.message})
        except (BrokenPipeError, ConnectionResetError):
            self.close_connection = True
        except Exception:
            if conn is not None:
                conn.rollback()
            self.close_connection = True
            self.send_json(500, {'error': 'Internal server error'})
        finally:
            if conn is not None:
                conn.close()

    def route(self, conn, path, parts, query, body):
        if path == '/api/inboxes':
            self.require_method('GET', 'POST')
            if self.command == 'GET':
                rows = conn.execute('SELECT id,name,created_at FROM inboxes ORDER BY created_at DESC,id DESC').fetchall()
                return 200, {'inboxes': [dict(row) for row in rows]}
            value = self.object_body(body)
            name = value.get('name')
            if not isinstance(name, str) or not 1 <= len(name.strip()) <= 80:
                raise HTTPError(400, 'Invalid name')
            name = name.strip()
            inbox_id = secrets.token_urlsafe(24)
            token = secrets.token_urlsafe(32)
            created = now_text()
            digest = hashlib.sha256(token.encode('utf-8')).hexdigest()
            conn.execute('INSERT INTO inboxes (id,name,token_sha256,created_at) VALUES (?,?,?,?)',
                         (inbox_id, name, digest, created))
            conn.commit()
            return 201, {'id': inbox_id, 'name': name, 'created_at': created, 'token': token}

        if len(parts) == 4 and parts[1] == 'hooks' and parts[2] and parts[3]:
            self.require_method('POST')
            inbox_id, token = parts[2], parts[3]
            row = conn.execute('SELECT token_sha256 FROM inboxes WHERE id=?', (inbox_id,)).fetchone()
            digest = hashlib.sha256(token.encode('utf-8')).hexdigest()
            stored = row['token_sha256'] if row is not None else '0' * 64
            matches = hmac.compare_digest(digest, stored)
            if row is None or not matches:
                raise HTTPError(404, 'Inbox not found')
            headers = {}
            for key in ('content-type', 'x-webhook-id', 'x-event-type'):
                value = self.headers.get(key)
                if value is not None:
                    headers[key] = value
            external_id = self.headers.get('X-Webhook-Id') or None
            body_hash = hashlib.sha256(body.encode('utf-8')).hexdigest()
            conn.execute('BEGIN IMMEDIATE')
            if external_id is not None:
                existing = conn.execute('SELECT id,body FROM events WHERE inbox_id=? AND external_id=?',
                                        (inbox_id, external_id)).fetchone()
                if existing is not None:
                    if existing['body'] != body:
                        raise HTTPError(409, 'Idempotency key has a different body')
                    conn.commit()
                    return 200, {'id': existing['id'], 'duplicate': True}
            event_id = secrets.token_urlsafe(24)
            conn.execute('''INSERT INTO events
                (id,inbox_id,external_id,received_at,headers_json,body,body_sha256)
                VALUES (?,?,?,?,?,?,?)''',
                (event_id, inbox_id, external_id, now_text(), json.dumps(headers), body, body_hash))
            conn.commit()
            return 201, {'id': event_id, 'duplicate': False}

        if len(parts) == 5 and parts[1:3] == ['api', 'inboxes'] and parts[3]:
            inbox_id, action = parts[3], parts[4]
            if action == 'events':
                self.require_method('GET')
                params = parse_qs(query, keep_blank_values=True)
                values = params.get('limit', ['20'])
                if len(values) != 1 or re.fullmatch(r'[0-9]+', values[0]) is None:
                    raise HTTPError(400, 'Invalid limit')
                text = values[0].lstrip('0') or '0'
                if len(text) > 3:
                    raise HTTPError(400, 'Invalid limit')
                limit = int(text)
                if not 1 <= limit <= 100:
                    raise HTTPError(400, 'Invalid limit')
                self.inbox(conn, inbox_id)
                rows = conn.execute('''SELECT id,inbox_id,external_id,received_at,body_sha256
                    FROM events WHERE inbox_id=? ORDER BY received_at DESC,id DESC LIMIT ?''',
                    (inbox_id, limit)).fetchall()
                return 200, {'events': [self.summary(row) for row in rows]}

            if action == 'retention':
                self.require_method('GET', 'PUT')
                if self.command == 'GET':
                    self.inbox(conn, inbox_id)
                    row = conn.execute('SELECT max_age_seconds FROM retention_policies WHERE inbox_id=?',
                                       (inbox_id,)).fetchone()
                    return 200, {'max_age_seconds': row[0] if row is not None else None}
                value = self.object_body(body)
                if set(value) != {'max_age_seconds'}:
                    raise HTTPError(400, 'Invalid retention policy')
                age = value['max_age_seconds']
                if age is not None and (type(age) is not int or not 1 <= age <= 2592000):
                    raise HTTPError(400, 'Invalid max_age_seconds')
                conn.execute('BEGIN IMMEDIATE')
                self.inbox(conn, inbox_id)
                if age is None:
                    conn.execute('DELETE FROM retention_policies WHERE inbox_id=?', (inbox_id,))
                else:
                    conn.execute('''INSERT INTO retention_policies (inbox_id,max_age_seconds)
                        VALUES (?,?) ON CONFLICT(inbox_id)
                        DO UPDATE SET max_age_seconds=excluded.max_age_seconds''', (inbox_id, age))
                conn.commit()
                return 200, {'max_age_seconds': age}

            if action == 'expire':
                self.require_method('POST')
                value = {} if body == '' else self.object_body(body)
                if set(value) - {'limit'}:
                    raise HTTPError(400, 'Invalid expiry request')
                limit = value.get('limit', 100)
                if type(limit) is not int or not 1 <= limit <= 100:
                    raise HTTPError(400, 'Invalid limit')
                conn.execute('BEGIN IMMEDIATE')
                self.inbox(conn, inbox_id)
                current = datetime.now(timezone.utc)
                policy = conn.execute('SELECT max_age_seconds FROM retention_policies WHERE inbox_id=?',
                                      (inbox_id,)).fetchone()
                deleted = 0
                if policy is not None:
                    cutoff = current - timedelta(seconds=policy[0])
                    rows = conn.execute('SELECT id,received_at FROM events WHERE inbox_id=?', (inbox_id,)).fetchall()
                    eligible = []
                    for row in rows:
                        received = instant(row['received_at'])
                        if received <= cutoff:
                            eligible.append((received, row['id']))
                    eligible.sort()
                    for received, event_id in eligible[:limit]:
                        cursor = conn.execute('DELETE FROM events WHERE id=? AND inbox_id=?', (event_id, inbox_id))
                        deleted += cursor.rowcount
                conn.commit()
                return 200, {'deleted': deleted}

        if len(parts) == 4 and parts[1:3] == ['api', 'events'] and parts[3]:
            self.require_method('GET')
            row = conn.execute('''SELECT id,inbox_id,external_id,received_at,body_sha256,body,headers_json
                FROM events WHERE id=?''', (parts[3],)).fetchone()
            if row is None:
                raise HTTPError(404, 'Event not found')
            result = self.summary(row)
            result['body'] = row['body']
            result['headers'] = json.loads(row['headers_json'])
            return 200, result

        if self.command not in ('GET', 'POST', 'PUT'):
            raise HTTPError(405, 'Method not allowed')
        raise HTTPError(404, 'Resource not found')


class Server(ThreadingHTTPServer):
    daemon_threads = True

    def handle_error(self, request, client_address):
        pass


def main():
    if not ADMIN_TOKEN:
        raise SystemExit('PHOENIX_ADMIN_TOKEN is required')
    try:
        initialize()
        port = int(os.environ.get('PORT', '8787'))
        server = Server(('127.0.0.1', port), Handler)
    except Exception:
        raise SystemExit('Unable to initialize HTTP service')
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == '__main__':
    main()
