import os
import json
import sqlite3
import hashlib
import hmac
import secrets
import datetime
import re
from urllib.parse import urlsplit, parse_qs, unquote
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

DB_PATH = os.environ.get('PHOENIX_DB_PATH', 'phoenix.sqlite')
ADMIN_TOKEN = os.environ.get('PHOENIX_ADMIN_TOKEN', '')
UTC = datetime.timezone.utc


def connect():
    db = sqlite3.connect(DB_PATH, timeout=30, isolation_level=None)
    db.row_factory = sqlite3.Row
    db.execute('PRAGMA foreign_keys=ON')
    db.execute('PRAGMA busy_timeout=30000')
    return db


def initialize():
    db = connect()
    try:
        db.execute('BEGIN IMMEDIATE')
        version = db.execute('PRAGMA user_version').fetchone()[0]
        if version not in (0, 1, 2):
            raise RuntimeError('Unsupported database version')
        if version in (0, 1):
            db.execute('''CREATE TABLE IF NOT EXISTS inboxes (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                token_sha256 TEXT NOT NULL,
                created_at TEXT NOT NULL
            )''')
            db.execute('''CREATE TABLE IF NOT EXISTS events (
                id TEXT PRIMARY KEY,
                inbox_id TEXT NOT NULL REFERENCES inboxes(id),
                external_id TEXT,
                received_at TEXT NOT NULL,
                headers_json TEXT NOT NULL,
                body TEXT NOT NULL,
                body_sha256 TEXT NOT NULL,
                UNIQUE(inbox_id, external_id)
            )''')
            db.execute('''CREATE TABLE IF NOT EXISTS retention_policies (
                inbox_id TEXT PRIMARY KEY REFERENCES inboxes(id),
                max_age_seconds INTEGER NOT NULL CHECK(max_age_seconds BETWEEN 1 AND 2592000)
            )''')
            db.execute('PRAGMA user_version=2')
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def timestamp():
    return datetime.datetime.now(UTC).isoformat()


def digest(value):
    return hashlib.sha256(value.encode('utf-8')).hexdigest()


def instant(value):
    if value.endswith('Z'):
        value = value[:-1] + '+00:00'
    result = datetime.datetime.fromisoformat(value)
    if result.tzinfo is None:
        result = result.replace(tzinfo=UTC)
    return result.astimezone(UTC)


SUMMARY = 'id, inbox_id, external_id, received_at, body_sha256'


class RequestError(Exception):
    def __init__(self, status, message):
        self.status = status
        self.message = message


class Handler(BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'

    def log_message(self, format, *args):
        pass

    def send_json(self, status, value):
        payload = json.dumps(value, ensure_ascii=False, separators=(',', ':')).encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(payload)))
        if self.close_connection:
            self.send_header('Connection', 'close')
        self.end_headers()
        self.wfile.write(payload)

    def send_error(self, code, message=None, explain=None):
        self.close_connection = True
        self.send_json(code, {'error': 'HTTP request rejected'})

    def handle_expect_100(self):
        self.send_response_only(100)
        self.end_headers()
        return True

    def read_body(self):
        self.close_connection = True
        if self.headers.get_all('Transfer-Encoding'):
            raise RequestError(400, 'Transfer-Encoding is not supported')
        lengths = self.headers.get_all('Content-Length', [])
        if not lengths:
            length = 0
        elif len(lengths) != 1 or re.fullmatch(r'[0-9]+', lengths[0]) is None:
            raise RequestError(400, 'Invalid Content-Length')
        else:
            raw_length = lengths[0].lstrip('0') or '0'
            if len(raw_length) > 5:
                raise RequestError(413, 'Body too large')
            length = int(raw_length)
        if length > 65536:
            raise RequestError(413, 'Body too large')
        self.connection.settimeout(30)
        raw = self.rfile.read(length)
        if len(raw) != length:
            raise RequestError(400, 'Incomplete body')
        try:
            return raw.decode('utf-8')
        except UnicodeDecodeError:
            raise RequestError(400, 'Invalid UTF-8')

    def parse_json(self, text):
        def invalid_constant(value):
            raise ValueError('Invalid constant')
        try:
            return json.loads(text, parse_constant=invalid_constant)
        except (ValueError, RecursionError):
            raise RequestError(400, 'Invalid JSON')

    def require_method(self, *methods):
        if self.command not in methods:
            raise RequestError(405, 'Method not allowed')

    def require_inbox(self, db, inbox_id):
        row = db.execute('SELECT id FROM inboxes WHERE id=?', (inbox_id,)).fetchone()
        if row is None:
            raise RequestError(404, 'Inbox not found')

    def dispatch(self, db, body):
        try:
            url = urlsplit(self.path)
        except ValueError:
            raise RequestError(400, 'Invalid URL')
        path = url.path
        if path.startswith('/api/'):
            values = self.headers.get_all('Authorization', [])
            expected = ('Bearer ' + ADMIN_TOKEN).encode('utf-8')
            supplied = values[0].encode('utf-8') if len(values) == 1 else b''
            if not hmac.compare_digest(supplied, expected):
                raise RequestError(401, 'Unauthorized')
        parts = [unquote(part) for part in path.split('/')]
        if path == '/health':
            self.require_method('GET')
            return 200, {'status': 'ok', 'schema_version': 2}
        if path == '/api/inboxes':
            self.require_method('GET', 'POST')
            if self.command == 'GET':
                rows = db.execute('SELECT id, name, created_at FROM inboxes ORDER BY created_at DESC, id DESC')
                return 200, {'inboxes': [dict(row) for row in rows]}
            data = self.parse_json(body)
            if not isinstance(data, dict) or not isinstance(data.get('name'), str):
                raise RequestError(400, 'Invalid name')
            name = data['name'].strip()
            if not 1 <= len(name) <= 80:
                raise RequestError(400, 'Invalid name')
            inbox_id, token, created = secrets.token_urlsafe(24), secrets.token_urlsafe(32), timestamp()
            db.execute('BEGIN IMMEDIATE')
            db.execute('INSERT INTO inboxes (id, name, token_sha256, created_at) VALUES (?, ?, ?, ?)',
                       (inbox_id, name, digest(token), created))
            db.commit()
            return 201, {'id': inbox_id, 'name': name, 'created_at': created, 'token': token}
        if len(parts) == 4 and parts[1] == 'hooks' and parts[2] and parts[3]:
            self.require_method('POST')
            inbox_id, token = parts[2:4]
            row = db.execute('SELECT token_sha256 FROM inboxes WHERE id=?', (inbox_id,)).fetchone()
            if row is None or not hmac.compare_digest(row['token_sha256'], digest(token)):
                raise RequestError(404, 'Hook not found')
            headers = {}
            for key in ('content-type', 'x-webhook-id', 'x-event-type'):
                value = self.headers.get(key)
                if value is not None:
                    headers[key] = value
            external = headers.get('x-webhook-id') or None
            body_hash = digest(body)
            db.execute('BEGIN IMMEDIATE')
            if external is not None:
                old = db.execute('SELECT id, body FROM events WHERE inbox_id=? AND external_id=?',
                                 (inbox_id, external)).fetchone()
                if old is not None:
                    if old['body'] != body:
                        raise RequestError(409, 'Idempotency key conflicts with body')
                    db.commit()
                    return 200, {'id': old['id'], 'duplicate': True}
            event_id = secrets.token_urlsafe(24)
            db.execute('''INSERT INTO events
                (id, inbox_id, external_id, received_at, headers_json, body, body_sha256)
                VALUES (?, ?, ?, ?, ?, ?, ?)''',
                (event_id, inbox_id, external, timestamp(), json.dumps(headers), body, body_hash))
            db.commit()
            return 201, {'id': event_id, 'duplicate': False}
        if len(parts) == 4 and parts[1:3] == ['api', 'events'] and parts[3]:
            self.require_method('GET')
            row = db.execute('SELECT ' + SUMMARY + ', body, headers_json FROM events WHERE id=?', (parts[3],)).fetchone()
            if row is None:
                raise RequestError(404, 'Event not found')
            result = dict(row)
            result['headers'] = json.loads(result.pop('headers_json'))
            return 200, result
        if len(parts) == 5 and parts[1:3] == ['api', 'inboxes'] and parts[3]:
            inbox_id, action = parts[3:5]
            if action == 'events':
                self.require_method('GET')
                query = parse_qs(url.query, keep_blank_values=True)
                values = query.get('limit', ['20'])
                if len(values) != 1 or re.fullmatch(r'[0-9]+', values[0]) is None:
                    raise RequestError(400, 'Invalid limit')
                limit_text = values[0].lstrip('0') or '0'
                if len(limit_text) > 3 or not 1 <= int(limit_text) <= 100:
                    raise RequestError(400, 'Invalid limit')
                self.require_inbox(db, inbox_id)
                rows = db.execute('SELECT ' + SUMMARY + ' FROM events WHERE inbox_id=? ORDER BY received_at DESC, id DESC LIMIT ?',
                                  (inbox_id, int(limit_text)))
                return 200, {'events': [dict(row) for row in rows]}
            if action == 'retention':
                self.require_method('GET', 'PUT')
                if self.command == 'GET':
                    self.require_inbox(db, inbox_id)
                    row = db.execute('SELECT max_age_seconds FROM retention_policies WHERE inbox_id=?', (inbox_id,)).fetchone()
                    return 200, {'max_age_seconds': row[0] if row else None}
                data = self.parse_json(body)
                if not isinstance(data, dict) or set(data) != {'max_age_seconds'}:
                    raise RequestError(400, 'Invalid retention policy')
                age = data['max_age_seconds']
                if age is not None and (type(age) is not int or not 1 <= age <= 2592000):
                    raise RequestError(400, 'Invalid retention policy')
                db.execute('BEGIN IMMEDIATE')
                self.require_inbox(db, inbox_id)
                if age is None:
                    db.execute('DELETE FROM retention_policies WHERE inbox_id=?', (inbox_id,))
                else:
                    db.execute('''INSERT INTO retention_policies (inbox_id, max_age_seconds) VALUES (?, ?)
                        ON CONFLICT(inbox_id) DO UPDATE SET max_age_seconds=excluded.max_age_seconds''', (inbox_id, age))
                db.commit()
                return 200, {'max_age_seconds': age}
            if action == 'expire':
                self.require_method('POST')
                data = {} if body == '' else self.parse_json(body)
                if not isinstance(data, dict) or not set(data).issubset({'limit'}):
                    raise RequestError(400, 'Invalid expiration request')
                limit = data.get('limit', 100)
                if type(limit) is not int or not 1 <= limit <= 100:
                    raise RequestError(400, 'Invalid limit')
                db.execute('BEGIN IMMEDIATE')
                self.require_inbox(db, inbox_id)
                now = datetime.datetime.now(UTC)
                policy = db.execute('SELECT max_age_seconds FROM retention_policies WHERE inbox_id=?', (inbox_id,)).fetchone()
                eligible = []
                if policy is not None:
                    cutoff = now - datetime.timedelta(seconds=policy[0])
                    for row in db.execute('SELECT id, received_at FROM events WHERE inbox_id=?', (inbox_id,)):
                        received = instant(row['received_at'])
                        if received <= cutoff:
                            eligible.append((received, row['id']))
                    eligible.sort()
                selected = eligible[:limit]
                deleted = 0
                for received, event_id in selected:
                    cursor = db.execute('DELETE FROM events WHERE id=? AND inbox_id=?', (event_id, inbox_id))
                    deleted += cursor.rowcount
                db.commit()
                return 200, {'deleted': deleted}
        raise RequestError(404, 'Not found')

    def handle_request(self):
        db = None
        try:
            body = self.read_body()
            db = connect()
            status, result = self.dispatch(db, body)
        except RequestError as exc:
            status, result = exc.status, {'error': exc.message}
        except (TimeoutError, OSError):
            status, result = 400, {'error': 'Unable to read request'}
        except Exception:
            status, result = 500, {'error': 'Internal server error'}
        finally:
            if db is not None:
                if db.in_transaction:
                    db.rollback()
                db.close()
        try:
            self.send_json(status, result)
        except (OSError, UnicodeError):
            self.close_connection = True

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


def main():
    if not ADMIN_TOKEN:
        raise SystemExit('PHOENIX_ADMIN_TOKEN is required')
    try:
        initialize()
        server = ThreadingHTTPServer(('127.0.0.1', int(os.environ.get('PORT', '8787'))), Handler)
    except Exception:
        raise SystemExit('Service initialization failed')
    server.daemon_threads = True
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == '__main__':
    main()
