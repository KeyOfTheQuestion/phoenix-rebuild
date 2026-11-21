import hashlib
import heapq
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


MAX_BODY = 65_536
MAX_AGE = 2_592_000
DECIMAL = re.compile(r'[0-9]+')
EVENT_FIELDS = 'id, inbox_id, external_id, received_at, body_sha256'
CAPTURE_HEADERS = ('content-type', 'x-webhook-id', 'x-event-type')
ROUTES = (
    (re.compile(r'/health'), {'GET': 'health'}),
    (re.compile(r'/api/inboxes'), {'GET': 'list_inboxes', 'POST': 'create_inbox'}),
    (re.compile(r'/hooks/([^/]+)/([^/]+)'), {'POST': 'receive_hook'}),
    (re.compile(r'/api/inboxes/([^/]+)/events'), {'GET': 'list_events'}),
    (re.compile(r'/api/events/([^/]+)'), {'GET': 'get_event'}),
    (re.compile(r'/api/inboxes/([^/]+)/retention'), {'GET': 'get_retention', 'PUT': 'put_retention'}),
    (re.compile(r'/api/inboxes/([^/]+)/expire'), {'POST': 'expire_events'}),
)
KNOWN_METHODS = frozenset(method for _, methods in ROUTES for method in methods)


class HTTPError(Exception):
    def __init__(self, status, message):
        super().__init__(message)
        self.status = status


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
            raise ValueError('Unsupported database schema version')
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


def utc_now():
    return datetime.now(timezone.utc)


def digest(data):
    return hashlib.sha256(data).hexdigest()


def unique_keys(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('Duplicate JSON key')
        result[key] = value
    return result


def invalid_constant(value):
    raise ValueError('Invalid JSON constant')


def read_object(body):
    try:
        value = json.loads(body, object_pairs_hook=unique_keys, parse_constant=invalid_constant)
    except (ValueError, RecursionError):
        raise HTTPError(400, 'Invalid JSON') from None
    if not isinstance(value, dict):
        raise HTTPError(400, 'Expected a JSON object')
    return value


def bounded_integer(value, maximum):
    return type(value) is int and 1 <= value <= maximum


def require_inbox(db, inbox_id):
    exists = db.execute('SELECT 1 FROM inboxes WHERE id = ?', (inbox_id,)).fetchone()
    if exists is None:
        raise HTTPError(404, 'Inbox not found')


def read_policy(db, inbox_id):
    row = db.execute(
        'SELECT max_age_seconds FROM retention_policies WHERE inbox_id = ?', (inbox_id,)
    ).fetchone()
    return None if row is None else row['max_age_seconds']


def parse_instant(value):
    if value.endswith('Z'):
        value = value[:-1] + '+00:00'
    instant = datetime.fromisoformat(value)
    if instant.tzinfo is None:
        instant = instant.replace(tzinfo=timezone.utc)
    return instant.astimezone(timezone.utc)


class Handler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        pass

    def send_error(self, code, message=None, explain=None):
        self.close_connection = True
        self.reply(code, {'error': self.responses.get(code, ('HTTP error',))[0]})

    def reply(self, status, payload, allow=None):
        data = json.dumps(payload, ensure_ascii=True, separators=(',', ':')).encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(data)))
        if self.close_connection:
            self.send_header('Connection', 'close')
        if allow is not None:
            self.send_header('Allow', ', '.join(allow))
        self.end_headers()
        if self.command != 'HEAD':
            self.wfile.write(data)

    def read_body(self):
        if self.headers.get_all('Transfer-Encoding') is not None:
            raise HTTPError(400, 'Transfer-Encoding is unsupported')
        lengths = self.headers.get_all('Content-Length', [])
        if not lengths:
            return ''
        if len(lengths) != 1 or DECIMAL.fullmatch(lengths[0].strip(' \t')) is None:
            raise HTTPError(400, 'Invalid Content-Length')
        digits = lengths[0].strip(' \t').lstrip('0') or '0'
        if len(digits) > len(str(MAX_BODY)) or int(digits) > MAX_BODY:
            raise HTTPError(413, 'Request body too large')
        length = int(digits)
        try:
            raw = self.rfile.read(length)
        except TimeoutError:
            raise HTTPError(400, 'Incomplete request body') from None
        if len(raw) != length:
            raise HTTPError(400, 'Incomplete request body')
        try:
            return raw.decode('utf-8')
        except UnicodeDecodeError:
            raise HTTPError(400, 'Invalid UTF-8') from None

    def authenticate(self):
        values = self.headers.get_all('Authorization', [])
        if len(values) != 1 or not hmac.compare_digest(
            values[0].encode('latin-1'), self.server.admin_authorization
        ):
            raise HTTPError(401, 'Unauthorized')

    def __getattr__(self, name):
        if name.startswith('do_'):
            return self.dispatch
        raise AttributeError(name)

    def dispatch(self):
        allow = None
        try:
            path, _, query = self.path.partition('?')
            if path.startswith('/api/'):
                self.authenticate()
            for pattern, methods in ROUTES:
                match = pattern.fullmatch(path)
                if match is None:
                    continue
                if self.command not in methods:
                    allow = methods
                    raise HTTPError(405, 'Method not allowed')
                body = self.read_body()
                endpoint = getattr(self, methods[self.command])
                with closing(connect(self.server.db_path)) as db:
                    status, payload = endpoint(db, body, query, *match.groups())
                break
            else:
                if self.command not in KNOWN_METHODS:
                    raise HTTPError(405, 'Method not allowed')
                raise HTTPError(404, 'Resource not found')
        except HTTPError as error:
            self.close_connection = True
            status, payload = error.status, {'error': str(error)}
        except Exception:
            self.close_connection = True
            status, payload = 500, {'error': 'Internal server error'}
        self.reply(status, payload, allow)

    def health(self, db, body, query):
        return 200, {'status': 'ok', 'schema_version': 2}

    def create_inbox(self, db, body, query):
        name = read_object(body).get('name')
        if not isinstance(name, str) or not 1 <= len(name.strip()) <= 80:
            raise HTTPError(400, 'Invalid inbox name')
        name = name.strip()
        try:
            name.encode('utf-8')
        except UnicodeEncodeError:
            raise HTTPError(400, 'Invalid inbox name') from None
        inbox_id = secrets.token_urlsafe(24)
        token = secrets.token_urlsafe(32)
        created_at = utc_now().isoformat(timespec='microseconds')
        with db:
            db.execute(
                'INSERT INTO inboxes (id, name, token_sha256, created_at) VALUES (?, ?, ?, ?)',
                (inbox_id, name, digest(token.encode('ascii')), created_at),
            )
        return 201, {'id': inbox_id, 'name': name, 'created_at': created_at, 'token': token}

    def list_inboxes(self, db, body, query):
        rows = db.execute('SELECT id, name, created_at FROM inboxes ORDER BY created_at DESC, id DESC')
        return 200, {'inboxes': [dict(row) for row in rows]}

    def receive_hook(self, db, body, query, inbox_id, token):
        header_values = {}
        for name in CAPTURE_HEADERS:
            if name in self.headers:
                header_values[name] = self.headers[name]
        external_id = header_values.get('x-webhook-id') or None
        body_hash = digest(body.encode('utf-8'))
        token_hash = digest(token.encode('utf-8'))
        with db:
            db.execute('BEGIN IMMEDIATE')
            inbox = db.execute('SELECT token_sha256 FROM inboxes WHERE id = ?', (inbox_id,)).fetchone()
            if inbox is None or not hmac.compare_digest(inbox['token_sha256'], token_hash):
                raise HTTPError(404, 'Inbox not found')
            if external_id is not None:
                prior = db.execute(
                    'SELECT id, body FROM events WHERE inbox_id = ? AND external_id = ?',
                    (inbox_id, external_id),
                ).fetchone()
                if prior is not None:
                    if prior['body'] != body:
                        raise HTTPError(409, 'Webhook ID already used with a different body')
                    return 200, {'id': prior['id'], 'duplicate': True}
            event_id = secrets.token_urlsafe(24)
            db.execute(
                '''INSERT INTO events
                   (id, inbox_id, external_id, received_at, headers_json, body, body_sha256)
                   VALUES (?, ?, ?, ?, ?, ?, ?)''',
                (event_id, inbox_id, external_id, utc_now().isoformat(timespec='microseconds'),
                 json.dumps(header_values), body, body_hash),
            )
        return 201, {'id': event_id, 'duplicate': False}

    def list_events(self, db, body, query, inbox_id):
        limits = parse_qs(query, keep_blank_values=True).get('limit', ['20'])
        if len(limits) != 1 or DECIMAL.fullmatch(limits[0]) is None:
            raise HTTPError(400, 'Invalid limit')
        digits = limits[0].lstrip('0') or '0'
        if len(digits) > 3 or not bounded_integer(int(digits), 100):
            raise HTTPError(400, 'Invalid limit')
        with db:
            db.execute('BEGIN')
            require_inbox(db, inbox_id)
            rows = db.execute(
                f'SELECT {EVENT_FIELDS} FROM events WHERE inbox_id = ? ORDER BY received_at DESC, id DESC LIMIT ?',
                (inbox_id, int(digits)),
            )
            events = [dict(row) for row in rows]
        return 200, {'events': events}

    def get_event(self, db, body, query, event_id):
        row = db.execute(
            f'SELECT {EVENT_FIELDS}, body, headers_json FROM events WHERE id = ?', (event_id,)
        ).fetchone()
        if row is None:
            raise HTTPError(404, 'Event not found')
        event = dict(row)
        event['headers'] = json.loads(event.pop('headers_json'))
        return 200, event

    def get_retention(self, db, body, query, inbox_id):
        with db:
            db.execute('BEGIN')
            require_inbox(db, inbox_id)
            age = read_policy(db, inbox_id)
        return 200, {'max_age_seconds': age}

    def put_retention(self, db, body, query, inbox_id):
        policy = read_object(body)
        if policy.keys() != {'max_age_seconds'}:
            raise HTTPError(400, 'Expected only max_age_seconds')
        age = policy['max_age_seconds']
        if age is not None and not bounded_integer(age, MAX_AGE):
            raise HTTPError(400, 'Invalid max_age_seconds')
        with db:
            db.execute('BEGIN IMMEDIATE')
            require_inbox(db, inbox_id)
            if age is None:
                db.execute('DELETE FROM retention_policies WHERE inbox_id = ?', (inbox_id,))
            else:
                db.execute(
                    '''INSERT INTO retention_policies (inbox_id, max_age_seconds) VALUES (?, ?)
                       ON CONFLICT(inbox_id) DO UPDATE SET max_age_seconds = excluded.max_age_seconds''',
                    (inbox_id, age),
                )
        return 200, policy

    def expire_events(self, db, body, query, inbox_id):
        options = read_object(body) if body else {}
        limit = options.get('limit', 100)
        if options.keys() - {'limit'} or not bounded_integer(limit, 100):
            raise HTTPError(400, 'Invalid expiration options')
        deleted = 0
        with db:
            db.execute('BEGIN IMMEDIATE')
            require_inbox(db, inbox_id)
            age = read_policy(db, inbox_id)
            if age is not None:
                cutoff = utc_now() - timedelta(seconds=age)
                with closing(db.execute('SELECT id, received_at FROM events WHERE inbox_id = ?', (inbox_id,))) as rows:
                    instants = ((parse_instant(row['received_at']), row['id']) for row in rows)
                    eligible = heapq.nsmallest(limit, (pair for pair in instants if pair[0] <= cutoff))
                if eligible:
                    placeholders = ', '.join('?' for _ in eligible)
                    ids = [event_id for _, event_id in eligible]
                    deleted = db.execute(
                        f'DELETE FROM events WHERE inbox_id = ? AND id IN ({placeholders})',
                        (inbox_id, *ids),
                    ).rowcount
        return 200, {'deleted': deleted}


class Server(ThreadingHTTPServer):
    def get_request(self):
        connection, address = super().get_request()
        connection.settimeout(15)
        return connection, address

    def handle_error(self, request, client_address):
        pass


def main():
    token = os.environ.get('PHOENIX_ADMIN_TOKEN')
    if not token:
        raise SystemExit('PHOENIX_ADMIN_TOKEN is required')
    path = os.environ.get('PHOENIX_DB_PATH', 'phoenix.sqlite')
    try:
        port = int(os.environ.get('PORT', '8787'))
        migrate(path)
        with Server(('127.0.0.1', port), Handler) as server:
            server.db_path = path
            server.admin_authorization = ('Bearer ' + token).encode('utf-8')
            server.serve_forever()
    except KeyboardInterrupt:
        pass
    except (OSError, OverflowError, sqlite3.Error, ValueError):
        raise SystemExit('Unable to start Phoenix') from None


if __name__ == '__main__':
    main()
