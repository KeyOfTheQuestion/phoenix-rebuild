import hashlib
import hmac
import json
import os
import re
import secrets
import sqlite3
from contextlib import closing, contextmanager
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, unquote


MAX_BODY_BYTES = 65536
MAX_RETENTION_SECONDS = 2592000
CAPTURED_HEADERS = ('content-type', 'x-webhook-id', 'x-event-type')
EVENT_FIELDS = 'id, inbox_id, external_id, received_at, body_sha256'
DECIMAL = re.compile(r'[0-9]+')
FRACTION = re.compile(r'([.,])([0-9]+)(?=Z$|[+-]|$)')
EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)


def utc_now():
    return datetime.now(timezone.utc)


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def connect(path):
    db = sqlite3.connect(path, timeout=10, isolation_level=None)
    db.row_factory = sqlite3.Row
    db.execute('PRAGMA foreign_keys=ON')
    return db


@contextmanager
def write_transaction(db):
    db.execute('BEGIN IMMEDIATE')
    try:
        yield
        db.commit()
    except BaseException:
        db.rollback()
        raise


def migrate(path):
    with closing(connect(path)) as db, write_transaction(db):
        version = db.execute('PRAGMA user_version').fetchone()[0]
        if version == 2:
            return
        if version not in (0, 1):
            raise SystemExit('Unsupported database schema version')
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


class HTTPError(Exception):
    def __init__(self, status, message):
        super().__init__(message)
        self.status = status


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
        value = json.loads(body, object_pairs_hook=unique_object,
                           parse_constant=invalid_constant)
    except (ValueError, RecursionError):
        raise HTTPError(400, 'Invalid JSON') from None
    if not isinstance(value, dict):
        raise HTTPError(400, 'Expected a JSON object')
    return value


def integer_in_range(value, maximum):
    return type(value) is int and 1 <= value <= maximum


def decimal_value(text, maximum):
    if DECIMAL.fullmatch(text) is None:
        return None
    digits = text.lstrip('0') or '0'
    if len(digits) > len(str(maximum)):
        return maximum + 1
    return int(digits)


def require_inbox(db, inbox_id):
    if db.execute('SELECT 1 FROM inboxes WHERE id = ?', (inbox_id,)).fetchone() is None:
        raise HTTPError(404, 'Inbox not found')


def get_policy(db, inbox_id):
    row = db.execute('SELECT max_age_seconds FROM retention_policies WHERE inbox_id = ?',
                     (inbox_id,)).fetchone()
    return None if row is None else row['max_age_seconds']


def epoch_microseconds(instant):
    delta = instant - EPOCH
    return (delta.days * 86400 + delta.seconds) * 1000000 + delta.microseconds


def parse_instant(value):
    # Keep sub-microsecond digits as a separate sort key to preserve exact ordering.
    if not isinstance(value, str):
        return None, ''
    fraction = FRACTION.search(value)
    tail = ''
    if fraction is not None:
        digits = fraction.group(2)
        tail = digits[6:].rstrip('0')
        value = value[:fraction.start()] + '.' + digits[:6].ljust(6, '0') + value[fraction.end():]
    if value.endswith('Z'):
        value = value[:-1] + '+00:00'
    try:
        instant = datetime.fromisoformat(value)
        if instant.tzinfo is None:
            instant = instant.replace(tzinfo=timezone.utc)
        return epoch_microseconds(instant), tail
    except (ValueError, OverflowError):
        return None, ''


def expire_batch(db, inbox_id, age, limit):
    cutoff = epoch_microseconds(utc_now() - timedelta(seconds=age))
    db.create_function('received_microseconds', 1, lambda value: parse_instant(value)[0],
                       deterministic=True)
    db.create_function('received_fraction_tail', 1, lambda value: parse_instant(value)[1],
                       deterministic=True)
    result = db.execute('''DELETE FROM events WHERE inbox_id = ? AND id IN (
        SELECT id FROM events
        WHERE inbox_id = ? AND (
            received_microseconds(received_at) < ? OR (
                received_microseconds(received_at) = ? AND received_fraction_tail(received_at) = ''
            )
        )
        ORDER BY received_microseconds(received_at), received_fraction_tail(received_at), id
        LIMIT ?
    )''', (inbox_id, inbox_id, cutoff, cutoff, limit))
    return result.rowcount


class Handler(BaseHTTPRequestHandler):
    def setup(self):
        self.request.settimeout(15)
        super().setup()

    def log_message(self, fmt, *args):
        pass

    def __getattr__(self, name):
        if name.startswith('do_'):
            return self.dispatch
        raise AttributeError(name)

    def send_error(self, code, message=None, explain=None):
        self.close_connection = True
        self.respond(code, {'error': self.responses.get(code, ('HTTP error',))[0]})

    def respond(self, status, value):
        payload = json.dumps(value, ensure_ascii=True, separators=(',', ':')).encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(payload)))
        if self.close_connection:
            self.send_header('Connection', 'close')
        self.end_headers()
        if getattr(self, 'command', None) != 'HEAD':
            self.wfile.write(payload)

    def authenticate(self):
        values = self.headers.get_all('Authorization', [])
        supplied = values[0].encode('latin-1') if len(values) == 1 else b''
        if not hmac.compare_digest(supplied, self.server.admin_authorization):
            raise HTTPError(401, 'Unauthorized')

    def read_body(self):
        if self.headers.get_all('Transfer-Encoding') is not None:
            raise HTTPError(400, 'Transfer-Encoding is unsupported')
        lengths = self.headers.get_all('Content-Length', [])
        if not lengths and self.command != 'PUT':
            return ''
        if len(lengths) != 1:
            raise HTTPError(400, 'Invalid Content-Length')
        length = decimal_value(lengths[0].strip(' \t'), MAX_BODY_BYTES)
        if length is None:
            raise HTTPError(400, 'Invalid Content-Length')
        if length > MAX_BODY_BYTES:
            raise HTTPError(413, 'Body too large')
        try:
            raw = self.rfile.read(length)
        except TimeoutError:
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
                self.authenticate()
            endpoint, identifier, allowed = self.route(path)
            if self.command not in allowed:
                raise HTTPError(405, 'Method not allowed')
            body = self.read_body()
            if endpoint == 'health':
                status, data = 200, {'status': 'ok', 'schema_version': 2}
            else:
                with closing(connect(self.server.db_path)) as db:
                    status, data = self.execute(db, endpoint, identifier, query, body)
        except HTTPError as error:
            self.close_connection = True
            status, data = error.status, {'error': str(error)}
        except (sqlite3.Error, ValueError, OverflowError, RecursionError):
            self.close_connection = True
            status, data = 500, {'error': 'Internal server error'}
        try:
            self.respond(status, data)
        except OSError:
            self.close_connection = True

    def route(self, path):
        if path == '/health':
            return 'health', None, ('GET',)
        if path == '/api/inboxes':
            return 'inboxes', None, ('GET', 'POST')
        parts = path.split('/')
        if len(parts) == 4 and parts[:2] == ['', 'hooks'] and parts[2] and parts[3]:
            return 'hook', (unquote(parts[2]), unquote(parts[3])), ('POST',)
        if len(parts) == 4 and parts[:3] == ['', 'api', 'events'] and parts[3]:
            return 'event', unquote(parts[3]), ('GET',)
        if len(parts) == 5 and parts[:3] == ['', 'api', 'inboxes'] and parts[3]:
            methods = {'events': ('GET',), 'retention': ('GET', 'PUT'), 'expire': ('POST',)}
            if parts[4] in methods:
                return parts[4], unquote(parts[3]), methods[parts[4]]
        if self.command not in ('GET', 'POST', 'PUT'):
            raise HTTPError(405, 'Method not allowed')
        raise HTTPError(404, 'Resource not found')

    def execute(self, db, endpoint, identifier, query, body):
        if endpoint == 'inboxes':
            if self.command == 'POST':
                return self.create_inbox(db, body)
            rows = db.execute('SELECT id, name, created_at FROM inboxes ORDER BY created_at DESC, id DESC')
            return 200, {'inboxes': [dict(row) for row in rows]}
        if endpoint == 'hook':
            return self.receive_hook(db, *identifier, body)
        if endpoint == 'events':
            return self.list_events(db, identifier, query)
        if endpoint == 'event':
            row = db.execute(f'SELECT {EVENT_FIELDS}, body, headers_json FROM events WHERE id = ?',
                             (identifier,)).fetchone()
            if row is None:
                raise HTTPError(404, 'Event not found')
            event = dict(row)
            event['headers'] = json.loads(event.pop('headers_json'))
            return 200, event
        if endpoint == 'retention':
            return self.retention(db, identifier, body)
        return self.expire(db, identifier, body)

    def create_inbox(self, db, body):
        name = json_object(body).get('name')
        if not isinstance(name, str) or not 1 <= len(name.strip()) <= 80:
            raise HTTPError(400, 'Invalid inbox name')
        name = name.strip()
        try:
            name.encode('utf-8')
        except UnicodeEncodeError:
            raise HTTPError(400, 'Invalid inbox name') from None
        inbox_id = secrets.token_urlsafe(24)
        token = secrets.token_urlsafe(32)
        created_at = utc_now().isoformat()
        with write_transaction(db):
            db.execute('INSERT INTO inboxes (id, name, token_sha256, created_at) VALUES (?, ?, ?, ?)',
                       (inbox_id, name, sha256(token.encode('ascii')), created_at))
        return 201, {'id': inbox_id, 'name': name, 'created_at': created_at, 'token': token}

    def receive_hook(self, db, inbox_id, token, body):
        row = db.execute('SELECT token_sha256 FROM inboxes WHERE id = ?', (inbox_id,)).fetchone()
        supplied = sha256(token.encode('utf-8'))
        expected = row['token_sha256'] if row is not None else '0' * 64
        if not hmac.compare_digest(supplied, expected) or row is None:
            raise HTTPError(404, 'Inbox not found')
        headers = {name: self.headers[name] for name in CAPTURED_HEADERS if name in self.headers}
        external_id = headers.get('x-webhook-id') or None
        body_hash = sha256(body.encode('utf-8'))
        with write_transaction(db):
            if external_id is not None:
                existing = db.execute('SELECT id, body FROM events WHERE inbox_id = ? AND external_id = ?',
                                      (inbox_id, external_id)).fetchone()
                if existing is not None:
                    if existing['body'] != body:
                        raise HTTPError(409, 'Webhook ID already used with a different body')
                    return 200, {'id': existing['id'], 'duplicate': True}
            event_id = secrets.token_urlsafe(24)
            db.execute('''INSERT INTO events
                (id, inbox_id, external_id, received_at, headers_json, body, body_sha256)
                VALUES (?, ?, ?, ?, ?, ?, ?)''',
                       (event_id, inbox_id, external_id, utc_now().isoformat(),
                        json.dumps(headers), body, body_hash))
        return 201, {'id': event_id, 'duplicate': False}

    def list_events(self, db, inbox_id, query):
        values = parse_qs(query, keep_blank_values=True).get('limit', ['20'])
        limit = decimal_value(values[0], 100) if len(values) == 1 else None
        if not integer_in_range(limit, 100):
            raise HTTPError(400, 'Invalid limit')
        require_inbox(db, inbox_id)
        rows = db.execute(f'''SELECT {EVENT_FIELDS} FROM events WHERE inbox_id = ?
            ORDER BY received_at DESC, id DESC LIMIT ?''', (inbox_id, limit))
        return 200, {'events': [dict(row) for row in rows]}

    def retention(self, db, inbox_id, body):
        if self.command == 'GET':
            require_inbox(db, inbox_id)
            return 200, {'max_age_seconds': get_policy(db, inbox_id)}
        policy = json_object(body)
        if policy.keys() != {'max_age_seconds'}:
            raise HTTPError(400, 'Expected only max_age_seconds')
        age = policy['max_age_seconds']
        if age is not None and not integer_in_range(age, MAX_RETENTION_SECONDS):
            raise HTTPError(400, 'Invalid max_age_seconds')
        with write_transaction(db):
            require_inbox(db, inbox_id)
            if age is None:
                db.execute('DELETE FROM retention_policies WHERE inbox_id = ?', (inbox_id,))
            else:
                db.execute('''INSERT INTO retention_policies (inbox_id, max_age_seconds) VALUES (?, ?)
                    ON CONFLICT(inbox_id) DO UPDATE SET max_age_seconds = excluded.max_age_seconds''',
                           (inbox_id, age))
        return 200, policy

    def expire(self, db, inbox_id, body):
        options = json_object(body) if body else {}
        limit = options.get('limit', 100)
        if options.keys() - {'limit'} or not integer_in_range(limit, 100):
            raise HTTPError(400, 'Invalid expiration options')
        with write_transaction(db):
            require_inbox(db, inbox_id)
            age = get_policy(db, inbox_id)
            deleted = 0 if age is None else expire_batch(db, inbox_id, age, limit)
        return 200, {'deleted': deleted}


class Server(ThreadingHTTPServer):
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
        return
    except (ValueError, OverflowError, OSError, sqlite3.Error):
        raise SystemExit('Unable to start Phoenix') from None


if __name__ == '__main__':
    main()
