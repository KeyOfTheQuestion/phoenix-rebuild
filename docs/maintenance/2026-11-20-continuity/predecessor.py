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
from urllib.parse import parse_qs, unquote


MAX_BODY = 65536
MAX_AGE = 2592000
EVENT_FIELDS = 'id, inbox_id, external_id, received_at, body_sha256'
CAPTURE_HEADERS = ('content-type', 'x-webhook-id', 'x-event-type')
DECIMAL = re.compile(r'[0-9]+')
ISO_FRACTION = re.compile(r'(?<=\d{2}:\d{2}:\d{2})[.,]([0-9]+)')
ROUTES = (
    (re.compile(r'/health'), {'GET': 'health'}),
    (re.compile(r'/api/inboxes'), {'GET': 'list_inboxes', 'POST': 'create_inbox'}),
    (re.compile(r'/hooks/([^/]+)/([^/]+)'), {'POST': 'receive_hook'}),
    (re.compile(r'/api/inboxes/([^/]+)/events'), {'GET': 'list_events'}),
    (re.compile(r'/api/events/([^/]+)'), {'GET': 'get_event'}),
    (re.compile(r'/api/inboxes/([^/]+)/retention'),
     {'GET': 'get_retention', 'PUT': 'put_retention'}),
    (re.compile(r'/api/inboxes/([^/]+)/expire'), {'POST': 'expire'}),
)
SCHEMA = (
    '''CREATE TABLE IF NOT EXISTS inboxes (
        id TEXT PRIMARY KEY,
        name TEXT NOT NULL,
        token_sha256 TEXT NOT NULL,
        created_at TEXT NOT NULL
    )''',
    '''CREATE TABLE IF NOT EXISTS events (
        id TEXT PRIMARY KEY,
        inbox_id TEXT NOT NULL REFERENCES inboxes(id),
        external_id TEXT,
        received_at TEXT NOT NULL,
        headers_json TEXT NOT NULL,
        body TEXT NOT NULL,
        body_sha256 TEXT NOT NULL,
        UNIQUE(inbox_id, external_id)
    )''',
    '''CREATE TABLE IF NOT EXISTS retention_policies (
        inbox_id TEXT PRIMARY KEY REFERENCES inboxes(id),
        max_age_seconds INTEGER NOT NULL CHECK(max_age_seconds BETWEEN 1 AND 2592000)
    )''',
)


class HTTPError(Exception):
    def __init__(self, status, message):
        super().__init__(message)
        self.status = status


def connect(path):
    db = sqlite3.connect(path, timeout=15)
    db.row_factory = sqlite3.Row
    db.execute('PRAGMA foreign_keys=ON')
    return db


def migrate(path):
    with closing(connect(path)) as db, db:
        db.execute('BEGIN IMMEDIATE')
        version = db.execute('PRAGMA user_version').fetchone()[0]
        if version not in (0, 1, 2):
            raise RuntimeError('Unsupported database schema version')
        if version < 2:
            for statement in SCHEMA:
                db.execute(statement)
            db.execute('PRAGMA user_version=2')


def utc_now():
    return datetime.now(timezone.utc)


def timestamp():
    return utc_now().isoformat(timespec='microseconds')


def instant_key(value):
    # Keep fractional digits separately to compare even sub-microsecond values.
    match = ISO_FRACTION.search(value)
    fraction = ''
    if match:
        fraction = match.group(1).rstrip('0')
        value = value[:match.start()] + value[match.end():]
    if value.endswith('Z'):
        value = value[:-1] + '+00:00'
    instant = datetime.fromisoformat(value)
    if instant.tzinfo is None:
        instant = instant.replace(tzinfo=timezone.utc)
    return instant.astimezone(timezone.utc), fraction


def sha256(value):
    return hashlib.sha256(value).hexdigest()


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('Duplicate JSON key')
        result[key] = value
    return result


def reject_constant(value):
    raise ValueError('Invalid JSON constant')


def json_object(body):
    try:
        value = json.loads(body, object_pairs_hook=unique_object,
                           parse_constant=reject_constant)
    except (ValueError, RecursionError):
        raise HTTPError(400, 'Invalid JSON') from None
    if not isinstance(value, dict):
        raise HTTPError(400, 'Expected a JSON object')
    return value


def require_inbox(db, inbox_id):
    if db.execute('SELECT 1 FROM inboxes WHERE id = ?', (inbox_id,)).fetchone() is None:
        raise HTTPError(404, 'Inbox not found')


def retention_policy(db, inbox_id):
    row = db.execute('SELECT max_age_seconds FROM retention_policies WHERE inbox_id = ?',
                     (inbox_id,)).fetchone()
    return row['max_age_seconds'] if row is not None else None


class Server(ThreadingHTTPServer):
    def __init__(self, address, db_path, admin_token):
        self.db_path = db_path
        self.authorization = ('Bearer ' + admin_token).encode('utf-8')
        super().__init__(address, Handler)

    def handle_error(self, request, client_address):
        pass


class Handler(BaseHTTPRequestHandler):
    def setup(self):
        self.request.settimeout(20)
        super().setup()

    def log_message(self, format, *args):
        pass

    def __getattr__(self, name):
        if name.startswith('do_'):
            return self.dispatch
        raise AttributeError(name)

    def respond(self, status, payload, allow=None):
        self.close_connection = True
        data = json.dumps(payload, ensure_ascii=True, separators=(',', ':')).encode('utf-8')
        if getattr(self, 'request_version', None) == 'HTTP/0.9':
            self.request_version = 'HTTP/1.0'
        self.send_response(status)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(data)))
        self.send_header('Connection', 'close')
        if allow is not None:
            self.send_header('Allow', allow)
        self.end_headers()
        if getattr(self, 'command', None) != 'HEAD':
            self.wfile.write(data)

    def send_error(self, code, message=None, explain=None):
        if code == 501:
            code = 405
        self.respond(code, {'error': self.responses.get(code, ('HTTP error',))[0]})

    def body(self):
        if self.headers.get_all('Transfer-Encoding') is not None:
            raise HTTPError(400, 'Transfer-Encoding is unsupported')
        lengths = self.headers.get_all('Content-Length', [])
        if not lengths:
            if self.command == 'PUT':
                raise HTTPError(400, 'Missing Content-Length')
            return ''
        if len(lengths) != 1 or not DECIMAL.fullmatch(lengths[0].strip(' \t')):
            raise HTTPError(400, 'Invalid Content-Length')
        digits = lengths[0].strip(' \t').lstrip('0') or '0'
        if len(digits) > 5 or int(digits) > MAX_BODY:
            raise HTTPError(413, 'Body too large')
        length = int(digits)
        try:
            raw = self.rfile.read(length)
        except (TimeoutError, ConnectionError):
            raise HTTPError(400, 'Incomplete body') from None
        if len(raw) != length:
            raise HTTPError(400, 'Incomplete body')
        try:
            return raw.decode('utf-8')
        except UnicodeDecodeError:
            raise HTTPError(400, 'Invalid UTF-8') from None

    def dispatch(self):
        try:
            path, _, query = self.path.partition('?')
            if path.startswith('/api/'):
                credentials = self.headers.get_all('Authorization', [])
                if len(credentials) != 1 or not hmac.compare_digest(
                    credentials[0].encode('latin-1'), self.server.authorization
                ):
                    raise HTTPError(401, 'Unauthorized')
            for pattern, methods in ROUTES:
                match = pattern.fullmatch(path)
                if match:
                    break
            else:
                if self.command not in ('GET', 'POST', 'PUT'):
                    raise HTTPError(405, 'Method not allowed')
                raise HTTPError(404, 'Not found')
            action = methods.get(self.command)
            if action is None:
                self.respond(405, {'error': 'Method not allowed'}, ', '.join(methods))
                return
            body = self.body()
            parts = tuple(unquote(part, errors='strict') for part in match.groups())
            if action == 'health':
                status, payload = 200, {'status': 'ok', 'schema_version': 2}
            else:
                with closing(connect(self.server.db_path)) as db, db:
                    status, payload = getattr(self, action)(db, body, query, *parts)
        except HTTPError as error:
            status, payload = error.status, {'error': str(error)}
        except UnicodeDecodeError:
            status, payload = 400, {'error': 'Invalid URL encoding'}
        except Exception:
            status, payload = 500, {'error': 'Internal server error'}
        self.respond(status, payload)

    def create_inbox(self, db, body, query):
        name = json_object(body).get('name')
        if not isinstance(name, str) or not 1 <= len(name.strip()) <= 80:
            raise HTTPError(400, 'Invalid inbox name')
        name = name.strip()
        try:
            name.encode('utf-8')
        except UnicodeEncodeError:
            raise HTTPError(400, 'Invalid inbox name') from None
        inbox_id = secrets.token_urlsafe(18)
        token = secrets.token_urlsafe(32)
        created_at = timestamp()
        db.execute('INSERT INTO inboxes (id, name, token_sha256, created_at) VALUES (?, ?, ?, ?)',
                   (inbox_id, name, sha256(token.encode('ascii')), created_at))
        return 201, {'id': inbox_id, 'name': name, 'created_at': created_at, 'token': token}

    def list_inboxes(self, db, body, query):
        rows = db.execute('SELECT id, name, created_at FROM inboxes ORDER BY created_at DESC, id DESC')
        return 200, {'inboxes': [dict(row) for row in rows]}

    def receive_hook(self, db, body, query, inbox_id, token):
        row = db.execute('SELECT token_sha256 FROM inboxes WHERE id = ?', (inbox_id,)).fetchone()
        if row is None or not hmac.compare_digest(row['token_sha256'], sha256(token.encode('utf-8'))):
            raise HTTPError(404, 'Inbox not found')
        headers = {name: self.headers[name] for name in CAPTURE_HEADERS if name in self.headers}
        external_id = headers.get('x-webhook-id') or None
        db.execute('BEGIN IMMEDIATE')
        if external_id is not None:
            existing = db.execute(
                'SELECT id, body FROM events WHERE inbox_id = ? AND external_id = ?',
                (inbox_id, external_id),
            ).fetchone()
            if existing is not None:
                if existing['body'] != body:
                    raise HTTPError(409, 'Idempotency key already used with a different body')
                return 200, {'id': existing['id'], 'duplicate': True}
        event_id = secrets.token_urlsafe(18)
        db.execute(
            '''INSERT INTO events (id, inbox_id, external_id, received_at, headers_json, body, body_sha256)
               VALUES (?, ?, ?, ?, ?, ?, ?)''',
            (event_id, inbox_id, external_id, timestamp(), json.dumps(headers), body,
             sha256(body.encode('utf-8'))),
        )
        return 201, {'id': event_id, 'duplicate': False}

    def list_events(self, db, body, query, inbox_id):
        require_inbox(db, inbox_id)
        values = parse_qs(query, keep_blank_values=True).get('limit', ['20'])
        if len(values) != 1 or not DECIMAL.fullmatch(values[0]):
            raise HTTPError(400, 'Invalid limit')
        digits = values[0].lstrip('0') or '0'
        if len(digits) > 3 or not 1 <= int(digits) <= 100:
            raise HTTPError(400, 'Invalid limit')
        rows = db.execute(
            f'SELECT {EVENT_FIELDS} FROM events WHERE inbox_id = ? ORDER BY received_at DESC, id DESC LIMIT ?',
            (inbox_id, int(digits)),
        )
        return 200, {'events': [dict(row) for row in rows]}

    def get_event(self, db, body, query, event_id):
        row = db.execute(f'SELECT {EVENT_FIELDS}, body, headers_json FROM events WHERE id = ?',
                         (event_id,)).fetchone()
        if row is None:
            raise HTTPError(404, 'Event not found')
        event = dict(row)
        event['headers'] = json.loads(event.pop('headers_json'))
        return 200, event

    def get_retention(self, db, body, query, inbox_id):
        require_inbox(db, inbox_id)
        return 200, {'max_age_seconds': retention_policy(db, inbox_id)}

    def put_retention(self, db, body, query, inbox_id):
        db.execute('BEGIN IMMEDIATE')
        require_inbox(db, inbox_id)
        policy = json_object(body)
        if policy.keys() != {'max_age_seconds'}:
            raise HTTPError(400, 'Expected only max_age_seconds')
        age = policy['max_age_seconds']
        if age is None:
            db.execute('DELETE FROM retention_policies WHERE inbox_id = ?', (inbox_id,))
        elif type(age) is int and 1 <= age <= MAX_AGE:
            db.execute(
                '''INSERT INTO retention_policies (inbox_id, max_age_seconds) VALUES (?, ?)
                   ON CONFLICT(inbox_id) DO UPDATE SET max_age_seconds = excluded.max_age_seconds''',
                (inbox_id, age),
            )
        else:
            raise HTTPError(400, 'Invalid max_age_seconds')
        return 200, policy

    def expire(self, db, body, query, inbox_id):
        db.execute('BEGIN IMMEDIATE')
        require_inbox(db, inbox_id)
        options = json_object(body) if body else {}
        limit = options.get('limit', 100)
        if options.keys() - {'limit'} or type(limit) is not int or not 1 <= limit <= 100:
            raise HTTPError(400, 'Invalid expiration options')
        age = retention_policy(db, inbox_id)
        if age is None:
            return 200, {'deleted': 0}
        cutoff = instant_key((utc_now() - timedelta(seconds=age)).isoformat())
        rows = db.execute('SELECT id, received_at FROM events WHERE inbox_id = ?', (inbox_id,))

        def eligible():
            for row in rows:
                instant = instant_key(row['received_at'])
                if instant <= cutoff:
                    yield instant, row['id']

        oldest = heapq.nsmallest(limit, eligible())
        deleted = db.executemany('DELETE FROM events WHERE inbox_id = ? AND id = ?',
                                 ((inbox_id, event_id) for _, event_id in oldest)).rowcount
        return 200, {'deleted': deleted}


def main():
    token = os.environ.get('PHOENIX_ADMIN_TOKEN')
    if not token:
        raise SystemExit('PHOENIX_ADMIN_TOKEN is required')
    db_path = os.environ.get('PHOENIX_DB_PATH', 'phoenix.sqlite')
    try:
        port = int(os.environ.get('PORT', '8787'))
        migrate(db_path)
        with Server(('127.0.0.1', port), db_path, token) as server:
            server.serve_forever()
    except KeyboardInterrupt:
        pass
    except (OSError, ValueError, OverflowError, RuntimeError, sqlite3.Error):
        raise SystemExit('Phoenix startup failed') from None


if __name__ == '__main__':
    main()
