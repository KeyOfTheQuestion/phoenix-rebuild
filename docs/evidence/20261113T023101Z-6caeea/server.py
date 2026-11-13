import os
import sys
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
    conn = sqlite3.connect(DB_PATH, timeout=30, isolation_level=None)
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
            raise RuntimeError('Unsupported database version')
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


def summary(row):
    return {key: row[key] for key in (
        'id', 'inbox_id', 'external_id', 'received_at', 'body_sha256'
    )}


def strict_json(text):
    def reject_constant(value):
        raise ValueError('Invalid JSON constant')

    def object_pairs(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError('Duplicate JSON key')
            result[key] = value
        return result

    return json.loads(text, parse_constant=reject_constant, object_pairs_hook=object_pairs)


class RequestError(Exception):
    def __init__(self, status, message):
        self.status = status
        self.message = message


class Handler(BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'
    server_version = 'Phoenix'
    sys_version = ''

    def log_message(self, format, *args):
        pass

    def send_json(self, status, value):
        data = json.dumps(value, ensure_ascii=False, separators=(',', ':')).encode('utf-8')
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
        self.send_json(code, {'error': 'Invalid request' if message is None else message})

    def read_body(self):
        if self.headers.get_all('Transfer-Encoding') is not None:
            raise RequestError(400, 'Transfer-Encoding is not supported')
        lengths = self.headers.get_all('Content-Length', [])
        if not lengths:
            if self.command == 'PUT':
                raise RequestError(400, 'Content-Length required')
            length = 0
        elif len(lengths) != 1 or re.fullmatch(r'[0-9]+', lengths[0].strip()) is None:
            raise RequestError(400, 'Invalid Content-Length')
        else:
            try:
                length = int(lengths[0].strip())
            except ValueError:
                raise RequestError(400, 'Invalid Content-Length')
        if length > MAX_BODY:
            raise RequestError(413, 'Body too large')
        raw = self.rfile.read(length)
        if len(raw) != length:
            raise RequestError(400, 'Incomplete body')
        try:
            return raw.decode('utf-8')
        except UnicodeDecodeError:
            raise RequestError(400, 'Invalid UTF-8')

    def json_object(self, text):
        try:
            value = strict_json(text)
        except (ValueError, TypeError, RecursionError):
            raise RequestError(400, 'Invalid JSON')
        if not isinstance(value, dict):
            raise RequestError(400, 'Expected a JSON object')
        return value

    def require_inbox(self, conn, inbox_id):
        row = conn.execute('SELECT id FROM inboxes WHERE id=?', (inbox_id,)).fetchone()
        if row is None:
            raise RequestError(404, 'Inbox not found')

    def handle_request(self):
        conn = None
        try:
            parsed = urlsplit(self.path)
            path = parsed.path
            if path.startswith('/api/'):
                values = self.headers.get_all('Authorization', [])
                expected = ('Bearer ' + ADMIN_TOKEN).encode('utf-8')
                if len(values) != 1 or not hmac.compare_digest(values[0].encode('utf-8'), expected):
                    raise RequestError(401, 'Unauthorized')
            text = self.read_body()
            parts = [unquote(part) for part in path.split('/')]
            route = None
            allowed = ()
            if path == '/health':
                route, allowed = 'health', ('GET',)
            elif path == '/api/inboxes':
                route, allowed = 'inboxes', ('GET', 'POST')
            elif len(parts) == 5 and parts[:3] == ['', 'api', 'inboxes'] and parts[3]:
                if parts[4] in ('events', 'retention', 'expire'):
                    route = parts[4]
                    allowed = {'events': ('GET',), 'retention': ('GET', 'PUT'), 'expire': ('POST',)}[route]
            elif len(parts) == 4 and parts[:3] == ['', 'api', 'events'] and parts[3]:
                route, allowed = 'event', ('GET',)
            elif len(parts) == 4 and parts[:2] == ['', 'hooks'] and parts[2] and parts[3]:
                route, allowed = 'hook', ('POST',)
            if route is None:
                raise RequestError(404, 'Not found')
            if self.command not in allowed:
                raise RequestError(405, 'Method not allowed')
            if route == 'health':
                self.send_json(200, {'status': 'ok', 'schema_version': 2})
                return
            conn = connect()
            if route == 'inboxes':
                if self.command == 'GET':
                    rows = conn.execute('SELECT id,name,created_at FROM inboxes ORDER BY created_at DESC,id DESC').fetchall()
                    self.send_json(200, {'inboxes': [dict(row) for row in rows]})
                else:
                    value = self.json_object(text)
                    name = value.get('name')
                    if not isinstance(name, str) or not 1 <= len(name.strip()) <= 80:
                        raise RequestError(400, 'Invalid name')
                    name = name.strip()
                    inbox_id = secrets.token_urlsafe(24)
                    token = secrets.token_urlsafe(32)
                    created_at = now_text()
                    digest = hashlib.sha256(token.encode('utf-8')).hexdigest()
                    conn.execute('BEGIN IMMEDIATE')
                    conn.execute('INSERT INTO inboxes (id,name,token_sha256,created_at) VALUES (?,?,?,?)',
                                 (inbox_id, name, digest, created_at))
                    conn.commit()
                    self.send_json(201, {'id': inbox_id, 'name': name, 'created_at': created_at, 'token': token})
            elif route == 'hook':
                inbox_id, token = parts[2], parts[3]
                conn.execute('BEGIN IMMEDIATE')
                inbox = conn.execute('SELECT token_sha256 FROM inboxes WHERE id=?', (inbox_id,)).fetchone()
                digest = hashlib.sha256(token.encode('utf-8')).hexdigest()
                if inbox is None or not hmac.compare_digest(inbox['token_sha256'], digest):
                    raise RequestError(404, 'Not found')
                external_id = self.headers.get('X-Webhook-Id') or None
                body_hash = hashlib.sha256(text.encode('utf-8')).hexdigest()
                if external_id is not None:
                    existing = conn.execute('SELECT id,body FROM events WHERE inbox_id=? AND external_id=?',
                                            (inbox_id, external_id)).fetchone()
                    if existing is not None:
                        if existing['body'] != text:
                            raise RequestError(409, 'Idempotency key conflict')
                        conn.commit()
                        self.send_json(200, {'id': existing['id'], 'duplicate': True})
                        return
                headers = {}
                for key in ('content-type', 'x-webhook-id', 'x-event-type'):
                    value = self.headers.get(key)
                    if value is not None:
                        headers[key] = value
                event_id = secrets.token_urlsafe(24)
                conn.execute('''INSERT INTO events
                    (id,inbox_id,external_id,received_at,headers_json,body,body_sha256)
                    VALUES (?,?,?,?,?,?,?)''',
                    (event_id, inbox_id, external_id, now_text(), json.dumps(headers), text, body_hash))
                conn.commit()
                self.send_json(201, {'id': event_id, 'duplicate': False})
            elif route == 'event':
                row = conn.execute('SELECT * FROM events WHERE id=?', (parts[3],)).fetchone()
                if row is None:
                    raise RequestError(404, 'Event not found')
                value = summary(row)
                value['body'] = row['body']
                value['headers'] = json.loads(row['headers_json'])
                self.send_json(200, value)
            elif route == 'events':
                self.require_inbox(conn, parts[3])
                query = parse_qs(parsed.query, keep_blank_values=True)
                limits = query.get('limit', ['20'])
                if len(limits) != 1 or re.fullmatch(r'[0-9]+', limits[0]) is None:
                    raise RequestError(400, 'Invalid limit')
                try:
                    limit = int(limits[0])
                except ValueError:
                    raise RequestError(400, 'Invalid limit')
                if not 1 <= limit <= 100:
                    raise RequestError(400, 'Invalid limit')
                rows = conn.execute('''SELECT id,inbox_id,external_id,received_at,body_sha256
                    FROM events WHERE inbox_id=? ORDER BY received_at DESC,id DESC LIMIT ?''',
                    (parts[3], limit)).fetchall()
                self.send_json(200, {'events': [summary(row) for row in rows]})
            elif route == 'retention':
                inbox_id = parts[3]
                if self.command == 'GET':
                    self.require_inbox(conn, inbox_id)
                    row = conn.execute('SELECT max_age_seconds FROM retention_policies WHERE inbox_id=?', (inbox_id,)).fetchone()
                    self.send_json(200, {'max_age_seconds': row[0] if row is not None else None})
                else:
                    value = self.json_object(text)
                    if set(value) != {'max_age_seconds'}:
                        raise RequestError(400, 'Invalid retention policy')
                    age = value['max_age_seconds']
                    if age is not None and (type(age) is not int or not 1 <= age <= 2592000):
                        raise RequestError(400, 'Invalid max_age_seconds')
                    conn.execute('BEGIN IMMEDIATE')
                    self.require_inbox(conn, inbox_id)
                    if age is None:
                        conn.execute('DELETE FROM retention_policies WHERE inbox_id=?', (inbox_id,))
                    else:
                        conn.execute('''INSERT INTO retention_policies (inbox_id,max_age_seconds) VALUES (?,?)
                            ON CONFLICT(inbox_id) DO UPDATE SET max_age_seconds=excluded.max_age_seconds''', (inbox_id, age))
                    conn.commit()
                    self.send_json(200, {'max_age_seconds': age})
            elif route == 'expire':
                value = {} if text == '' else self.json_object(text)
                if set(value) - {'limit'}:
                    raise RequestError(400, 'Invalid expiry request')
                limit = value.get('limit', 100)
                if type(limit) is not int or not 1 <= limit <= 100:
                    raise RequestError(400, 'Invalid limit')
                inbox_id = parts[3]
                conn.execute('BEGIN IMMEDIATE')
                self.require_inbox(conn, inbox_id)
                policy = conn.execute('SELECT max_age_seconds FROM retention_policies WHERE inbox_id=?', (inbox_id,)).fetchone()
                batch_now = datetime.now(timezone.utc)
                deleted = 0
                if policy is not None:
                    cutoff = batch_now - timedelta(seconds=policy[0])
                    rows = conn.execute('SELECT id,received_at FROM events WHERE inbox_id=?', (inbox_id,)).fetchall()
                    eligible = []
                    for row in rows:
                        received = instant(row['received_at'])
                        if received <= cutoff:
                            eligible.append((received, row['id']))
                    eligible.sort(key=lambda item: (item[0], item[1]))
                    for received, event_id in eligible[:limit]:
                        cursor = conn.execute('DELETE FROM events WHERE id=? AND inbox_id=?', (event_id, inbox_id))
                        deleted += cursor.rowcount
                conn.commit()
                self.send_json(200, {'deleted': deleted})
        except RequestError as exc:
            if conn is not None and conn.in_transaction:
                conn.rollback()
            self.close_connection = True
            self.send_json(exc.status, {'error': exc.message})
        except (BrokenPipeError, ConnectionResetError):
            self.close_connection = True
        except Exception:
            if conn is not None and conn.in_transaction:
                conn.rollback()
            self.close_connection = True
            self.send_json(500, {'error': 'Internal server error'})
        finally:
            if conn is not None:
                conn.close()

    do_GET = handle_request
    do_POST = handle_request
    do_PUT = handle_request
    do_DELETE = handle_request
    do_PATCH = handle_request
    do_HEAD = handle_request
    do_OPTIONS = handle_request
    do_TRACE = handle_request
    do_CONNECT = handle_request

    def __getattr__(self, name):
        if name.startswith('do_'):
            return self.handle_request
        raise AttributeError(name)


class Server(ThreadingHTTPServer):
    daemon_threads = True

    def handle_error(self, request, client_address):
        pass


def main():
    if not ADMIN_TOKEN:
        sys.stderr.write('PHOENIX_ADMIN_TOKEN is required\n')
        return 1
    try:
        port = int(os.environ.get('PORT', '8787'))
        initialize()
        server = Server(('127.0.0.1', port), Handler)
    except Exception:
        sys.stderr.write('Unable to initialize Phoenix service\n')
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
