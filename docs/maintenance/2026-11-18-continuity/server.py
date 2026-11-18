import hashlib
import hmac
import json
import os
import re
import secrets
import sqlite3
from contextlib import closing
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs


MAX_BODY = 65536
MAX_AGE = 2592000
EVENT_FIELDS = 'id, inbox_id, external_id, received_at, body_sha256'
CAPTURED_HEADERS = ('content-type', 'x-webhook-id', 'x-event-type')
DECIMAL = re.compile(r'[0-9]+')
FRACTION = re.compile(r'([Tt ]\d{2}:\d{2}:\d{2})[.,]([0-9]+)')
ROUTES = (
    (re.compile(r'/health'), {'GET': 'health'}),
    (re.compile(r'/api/inboxes'), {'GET': 'list_inboxes', 'POST': 'create_inbox'}),
    (re.compile(r'/hooks/([^/]+)/([^/]+)'), {'POST': 'receive_hook'}),
    (re.compile(r'/api/inboxes/([^/]+)/events'), {'GET': 'list_events'}),
    (re.compile(r'/api/events/([^/]+)'), {'GET': 'get_event'}),
    (re.compile(r'/api/inboxes/([^/]+)/retention'), {'GET': 'get_retention', 'PUT': 'put_retention'}),
    (re.compile(r'/api/inboxes/([^/]+)/expire'), {'POST': 'expire_events'}),
)


class RequestError(Exception):
    def __init__(self, status, message):
        self.status = status
        self.message = message


def connect(path):
    db = sqlite3.connect(path, timeout=10)
    db.row_factory = sqlite3.Row
    db.execute('PRAGMA foreign_keys=ON')
    return db


def migrate(path):
    with closing(connect(path)) as db, db:
        db.execute('BEGIN IMMEDIATE')
        version = db.execute('PRAGMA user_version').fetchone()[0]
        if version == 2:
            return
        if version not in (0, 1):
            raise RuntimeError('Unsupported database schema version')
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


def now_utc():
    return datetime.now(timezone.utc)


def instant_key(value):
    # Normalize the whole seconds to UTC and preserve the fractional precision.
    fraction_match = FRACTION.search(value)
    fraction = ''
    if fraction_match:
        fraction = fraction_match.group(2).rstrip('0')
        value = value[:fraction_match.end(1)] + value[fraction_match.end():]
    if value.endswith('Z'):
        value = value[:-1] + '+00:00'
    instant = datetime.fromisoformat(value)
    if instant.tzinfo is None:
        instant = instant.replace(tzinfo=timezone.utc)
    seconds = instant.astimezone(timezone.utc).replace(tzinfo=None).isoformat(timespec='seconds')
    return seconds + ('.' + fraction if fraction else '')


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def require_inbox(db, inbox_id):
    if db.execute('SELECT 1 FROM inboxes WHERE id = ?', (inbox_id,)).fetchone() is None:
        raise RequestError(404, 'Inbox not found')


def retention_policy(db, inbox_id):
    row = db.execute('''SELECT p.max_age_seconds
        FROM inboxes AS i LEFT JOIN retention_policies AS p ON p.inbox_id = i.id
        WHERE i.id = ?''', (inbox_id,)).fetchone()
    if row is None:
        raise RequestError(404, 'Inbox not found')
    return row['max_age_seconds']


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('Duplicate JSON key')
        result[key] = value
    return result


def invalid_constant(value):
    raise ValueError('Invalid JSON constant')


def json_object(body):
    try:
        result = json.loads(body, object_pairs_hook=unique_object, parse_constant=invalid_constant)
    except (ValueError, RecursionError):
        raise RequestError(400, 'Invalid JSON') from None
    if not isinstance(result, dict):
        raise RequestError(400, 'Expected a JSON object')
    return result


def bounded_decimal(value, upper):
    if not DECIMAL.fullmatch(value):
        return None
    digits = value.lstrip('0') or '0'
    if len(digits) > len(str(upper)):
        return upper + 1
    return int(digits)


class PhoenixServer(ThreadingHTTPServer):
    def handle_error(self, request, client_address):
        pass


class Handler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        pass

    def __getattr__(self, name):
        if name.startswith('do_'):
            return self.dispatch
        raise AttributeError(name)

    def send_error(self, code, message=None, explain=None):
        self.respond(code, {'error': self.responses.get(code, ('Request failed',))[0]})

    def respond(self, status, payload, allow=None):
        data = json.dumps(payload, ensure_ascii=True, separators=(',', ':')).encode('utf-8')
        self.close_connection = True
        self.send_response(status)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(data)))
        self.send_header('Connection', 'close')
        if allow is not None:
            self.send_header('Allow', allow)
        self.end_headers()
        if self.command != 'HEAD':
            self.wfile.write(data)

    def read_body(self):
        if self.headers.get_all('Transfer-Encoding') is not None:
            raise RequestError(400, 'Transfer-Encoding is unsupported')
        lengths = self.headers.get_all('Content-Length', [])
        if not lengths:
            if self.command == 'PUT':
                raise RequestError(400, 'Content-Length is required')
            return ''
        if len(lengths) != 1:
            raise RequestError(400, 'Invalid Content-Length')
        length = bounded_decimal(lengths[0].strip(' \t'), MAX_BODY)
        if length is None:
            raise RequestError(400, 'Invalid Content-Length')
        if length > MAX_BODY:
            raise RequestError(413, 'Body too large')
        data = self.rfile.read(length)
        if len(data) != length:
            raise RequestError(400, 'Incomplete body')
        try:
            return data.decode('utf-8')
        except UnicodeDecodeError:
            raise RequestError(400, 'Invalid UTF-8') from None

    def dispatch(self):
        path, _, query = self.path.partition('?')
        try:
            if path.startswith('/api/'):
                credentials = self.headers.get_all('Authorization', [])
                if len(credentials) != 1 or not hmac.compare_digest(
                    credentials[0].encode('latin-1'), self.server.admin_authorization
                ):
                    raise RequestError(401, 'Unauthorized')
            for pattern, methods in ROUTES:
                match = pattern.fullmatch(path)
                if match:
                    action = methods.get(self.command)
                    if action is None:
                        self.respond(405, {'error': 'Method not allowed'}, ', '.join(methods))
                        return
                    body = self.read_body()
                    with closing(connect(self.server.db_path)) as db:
                        status, payload = getattr(self, action)(db, body, query, *match.groups())
                    break
            else:
                raise RequestError(404, 'Not found')
        except RequestError as error:
            status, payload = error.status, {'error': error.message}
        except (TimeoutError, ConnectionError):
            status, payload = 400, {'error': 'Incomplete request'}
        except Exception:
            status, payload = 500, {'error': 'Internal server error'}
        self.respond(status, payload)

    def health(self, db, body, query):
        return 200, {'status': 'ok', 'schema_version': 2}

    def create_inbox(self, db, body, query):
        name = json_object(body).get('name')
        if not isinstance(name, str) or not 1 <= len(name.strip()) <= 80:
            raise RequestError(400, 'Invalid inbox name')
        name = name.strip()
        try:
            name.encode('utf-8')
        except UnicodeEncodeError:
            raise RequestError(400, 'Invalid inbox name') from None
        inbox_id = secrets.token_urlsafe(24)
        token = secrets.token_urlsafe(32)
        created_at = now_utc().isoformat(timespec='microseconds')
        with db:
            db.execute('''INSERT INTO inboxes (id, name, token_sha256, created_at)
                VALUES (?, ?, ?, ?)''', (inbox_id, name, sha256(token.encode('ascii')), created_at))
        return 201, {'id': inbox_id, 'name': name, 'created_at': created_at, 'token': token}

    def list_inboxes(self, db, body, query):
        rows = db.execute('SELECT id, name, created_at FROM inboxes ORDER BY created_at DESC, id DESC')
        return 200, {'inboxes': [dict(row) for row in rows]}

    def receive_hook(self, db, body, query, inbox_id, token):
        inbox = db.execute('SELECT token_sha256 FROM inboxes WHERE id = ?', (inbox_id,)).fetchone()
        if inbox is None or not hmac.compare_digest(inbox['token_sha256'], sha256(token.encode('utf-8'))):
            raise RequestError(404, 'Inbox not found')
        headers = {name: self.headers[name] for name in CAPTURED_HEADERS if name in self.headers}
        external_id = headers.get('x-webhook-id') or None
        body_digest = sha256(body.encode('utf-8'))
        with db:
            db.execute('BEGIN IMMEDIATE')
            if external_id is not None:
                old = db.execute('''SELECT id, body FROM events WHERE inbox_id = ? AND external_id = ?''',
                                 (inbox_id, external_id)).fetchone()
                if old is not None:
                    if old['body'] != body:
                        raise RequestError(409, 'Webhook ID already used with a different body')
                    return 200, {'id': old['id'], 'duplicate': True}
            event_id = secrets.token_urlsafe(24)
            received_at = now_utc().isoformat(timespec='microseconds')
            db.execute('''INSERT INTO events
                (id, inbox_id, external_id, received_at, headers_json, body, body_sha256)
                VALUES (?, ?, ?, ?, ?, ?, ?)''',
                       (event_id, inbox_id, external_id, received_at, json.dumps(headers), body, body_digest))
        return 201, {'id': event_id, 'duplicate': False}

    def list_events(self, db, body, query, inbox_id):
        require_inbox(db, inbox_id)
        limits = parse_qs(query, keep_blank_values=True).get('limit', ['20'])
        limit = bounded_decimal(limits[0], 100) if len(limits) == 1 else None
        if limit is None or not 1 <= limit <= 100:
            raise RequestError(400, 'Invalid limit')
        rows = db.execute(f'''SELECT {EVENT_FIELDS} FROM events WHERE inbox_id = ?
            ORDER BY received_at DESC, id DESC LIMIT ?''', (inbox_id, limit))
        return 200, {'events': [dict(row) for row in rows]}

    def get_event(self, db, body, query, event_id):
        row = db.execute(f'SELECT {EVENT_FIELDS}, body, headers_json FROM events WHERE id = ?',
                         (event_id,)).fetchone()
        if row is None:
            raise RequestError(404, 'Event not found')
        event = dict(row)
        event['headers'] = json.loads(event.pop('headers_json'))
        return 200, event

    def get_retention(self, db, body, query, inbox_id):
        return 200, {'max_age_seconds': retention_policy(db, inbox_id)}

    def put_retention(self, db, body, query, inbox_id):
        policy = json_object(body)
        if policy.keys() != {'max_age_seconds'}:
            raise RequestError(400, 'Expected exactly max_age_seconds')
        age = policy['max_age_seconds']
        if age is not None and (type(age) is not int or not 1 <= age <= MAX_AGE):
            raise RequestError(400, 'Invalid max_age_seconds')
        with db:
            db.execute('BEGIN IMMEDIATE')
            require_inbox(db, inbox_id)
            if age is None:
                db.execute('DELETE FROM retention_policies WHERE inbox_id = ?', (inbox_id,))
            else:
                db.execute('''INSERT INTO retention_policies (inbox_id, max_age_seconds) VALUES (?, ?)
                    ON CONFLICT(inbox_id) DO UPDATE SET max_age_seconds = excluded.max_age_seconds''',
                           (inbox_id, age))
        return 200, policy

    def expire_events(self, db, body, query, inbox_id):
        options = json_object(body) if body else {}
        limit = options.get('limit', 100)
        if options.keys() - {'limit'} or type(limit) is not int or not 1 <= limit <= 100:
            raise RequestError(400, 'Invalid expiration options')
        with db:
            db.execute('BEGIN IMMEDIATE')
            age = retention_policy(db, inbox_id)
            if age is None:
                return 200, {'deleted': 0}
            cutoff = instant_key((now_utc() - timedelta(seconds=age)).isoformat())
            db.create_function('instant_key', 1, instant_key, deterministic=True)
            rows = db.execute('''SELECT id, instant_key(received_at) AS received_instant
                FROM events WHERE inbox_id = ? AND instant_key(received_at) <= ?
                ORDER BY received_instant ASC, id ASC LIMIT ?''', (inbox_id, cutoff, limit)).fetchall()
            deleted = db.executemany('DELETE FROM events WHERE id = ? AND inbox_id = ?',
                                     ((row['id'], inbox_id) for row in rows)).rowcount
        return 200, {'deleted': deleted}


def main():
    token = os.environ.get('PHOENIX_ADMIN_TOKEN')
    if not token:
        raise SystemExit('PHOENIX_ADMIN_TOKEN is required')
    db_path = os.environ.get('PHOENIX_DB_PATH', 'phoenix.sqlite')
    try:
        port = int(os.environ.get('PORT', '8787'))
        migrate(db_path)
        server = PhoenixServer(('127.0.0.1', port), Handler)
    except RuntimeError as error:
        raise SystemExit(str(error)) from None
    except (ValueError, OverflowError, OSError, sqlite3.Error):
        raise SystemExit('Unable to start Phoenix') from None
    with server:
        server.db_path = db_path
        server.admin_authorization = ('Bearer ' + token).encode('utf-8')
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass


if __name__ == '__main__':
    main()
