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


MAX_BODY_BYTES = 65536
SUMMARY_COLUMNS = 'id, inbox_id, external_id, received_at, body_sha256'
CAPTURED_HEADERS = ('content-type', 'x-webhook-id', 'x-event-type')
ROUTES = (
    (re.compile(r'/health'), {'GET': 'health'}),
    (re.compile(r'/api/inboxes'), {'GET': 'list_inboxes', 'POST': 'create_inbox'}),
    (re.compile(r'/api/inboxes/([^/]+)/events'), {'GET': 'list_events'}),
    (re.compile(r'/api/events/([^/]+)'), {'GET': 'get_event'}),
    (re.compile(r'/api/inboxes/([^/]+)/retention'), {'GET': 'get_retention', 'PUT': 'put_retention'}),
    (re.compile(r'/api/inboxes/([^/]+)/expire'), {'POST': 'expire'}),
    (re.compile(r'/hooks/([^/]+)/([^/]+)'), {'POST': 'receive_hook'}),
)


class HTTPError(Exception):
    def __init__(self, status, message, close=False):
        super().__init__(message)
        self.status = status
        self.close = close


def connect(path):
    connection = sqlite3.connect(path, timeout=10)
    connection.row_factory = sqlite3.Row
    connection.execute('PRAGMA foreign_keys=ON')
    return connection


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


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def utc_now():
    return datetime.now(timezone.utc)


def instant_key(value):
    # Keep fractional precision beyond datetime's microseconds for stored values.
    if value.endswith('Z'):
        value = value[:-1] + '+00:00'
    fraction = re.search(r'[.,]([0-9]+)(?=[+-]|$)', value)
    digits = ''
    if fraction:
        digits = fraction.group(1).rstrip('0')
        value = value[:fraction.start()] + value[fraction.end():]
    return datetime.fromisoformat(value).astimezone(timezone.utc), digits


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('Duplicate JSON key')
        result[key] = value
    return result


def reject_constant(value):
    raise ValueError('Invalid JSON constant')


def json_object(text):
    try:
        value = json.loads(text, object_pairs_hook=unique_object, parse_constant=reject_constant)
    except (ValueError, RecursionError):
        raise HTTPError(400, 'Invalid JSON') from None
    if not isinstance(value, dict):
        raise HTTPError(400, 'Expected a JSON object')
    return value


def require_inbox(db, inbox_id):
    if db.execute('SELECT 1 FROM inboxes WHERE id = ?', (inbox_id,)).fetchone() is None:
        raise HTTPError(404, 'Inbox not found')


def retention_policy(db, inbox_id):
    row = db.execute('SELECT max_age_seconds FROM retention_policies WHERE inbox_id = ?', (inbox_id,)).fetchone()
    return row['max_age_seconds'] if row is not None else None


class Handler(BaseHTTPRequestHandler):
    # HTTP/1.0 responses close the connection, including after rejected bodies.
    protocol_version = 'HTTP/1.0'

    def log_message(self, format, *args):
        pass

    def __getattr__(self, name):
        if name.startswith('do_'):
            return self.dispatch
        raise AttributeError(name)

    def send_error(self, code, message=None, explain=None):
        self.close_connection = True
        self.send_json(code, {'error': 'Invalid HTTP request'})

    def send_json(self, status, payload, headers=None):
        data = json.dumps(payload, ensure_ascii=True, separators=(',', ':')).encode('utf-8')
        if self.request_version == 'HTTP/0.9':
            self.request_version = 'HTTP/1.0'
        self.send_response(status)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(data)))
        self.send_header('Connection', 'close')
        if headers:
            for key, value in headers.items():
                self.send_header(key, value)
        self.end_headers()
        self.close_connection = True
        if self.command != 'HEAD':
            self.wfile.write(data)

    def dispatch(self):
        path, _, query = self.path.partition('?')
        response_headers = None
        try:
            if path.startswith('/api/'):
                credentials = self.headers.get_all('Authorization', [])
                if len(credentials) != 1 or not hmac.compare_digest(
                    credentials[0].encode('latin-1'), self.server.authorization
                ):
                    raise HTTPError(401, 'Unauthorized')
            for pattern, methods in ROUTES:
                match = pattern.fullmatch(path)
                if match is not None:
                    break
            else:
                if self.command not in ('GET', 'POST', 'PUT'):
                    raise HTTPError(405, 'Method not allowed')
                raise HTTPError(404, 'Not found')
            if self.command not in methods:
                response_headers = {'Allow': ', '.join(methods)}
                raise HTTPError(405, 'Method not allowed')
            body, raw_body = self.read_body()
            endpoint = getattr(self, methods[self.command])
            with closing(connect(self.server.db_path)) as db, db:
                status, payload = endpoint(db, body, raw_body, query, *match.groups())
        except HTTPError as error:
            if error.close:
                self.close_connection = True
            status, payload = error.status, {'error': str(error)}
        except Exception:
            self.close_connection = True
            status, payload = 500, {'error': 'Internal server error'}
        self.send_json(status, payload, response_headers)

    def read_body(self):
        if self.headers.get_all('Transfer-Encoding') is not None:
            raise HTTPError(400, 'Transfer-Encoding is unsupported', close=True)
        lengths = self.headers.get_all('Content-Length', [])
        if not lengths:
            if self.command == 'PUT':
                raise HTTPError(400, 'Content-Length is required', close=True)
            return '', b''
        if len(lengths) != 1:
            raise HTTPError(400, 'Invalid Content-Length', close=True)
        length = lengths[0].strip(' \t')
        if not re.fullmatch(r'[0-9]+', length):
            raise HTTPError(400, 'Invalid Content-Length', close=True)
        digits = length.lstrip('0') or '0'
        if len(digits) > 5 or int(digits) > MAX_BODY_BYTES:
            raise HTTPError(413, 'Body too large', close=True)
        count = int(digits)
        data = self.rfile.read(count)
        if len(data) != count:
            raise HTTPError(400, 'Incomplete body', close=True)
        try:
            return data.decode('utf-8'), data
        except UnicodeDecodeError:
            raise HTTPError(400, 'Invalid UTF-8', close=True) from None

    def health(self, db, body, raw_body, query):
        return 200, {'status': 'ok', 'schema_version': 2}

    def create_inbox(self, db, body, raw_body, query):
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
        created_at = utc_now().isoformat(timespec='microseconds')
        db.execute('INSERT INTO inboxes (id, name, token_sha256, created_at) VALUES (?, ?, ?, ?)',
                   (inbox_id, name, sha256(token.encode('ascii')), created_at))
        return 201, {'id': inbox_id, 'name': name, 'created_at': created_at, 'token': token}

    def list_inboxes(self, db, body, raw_body, query):
        rows = db.execute('SELECT id, name, created_at FROM inboxes ORDER BY created_at DESC, id DESC')
        return 200, {'inboxes': [dict(row) for row in rows]}

    def receive_hook(self, db, body, raw_body, query, inbox_id, token):
        row = db.execute('SELECT token_sha256 FROM inboxes WHERE id = ?', (inbox_id,)).fetchone()
        if row is None or not hmac.compare_digest(row['token_sha256'], sha256(token.encode('utf-8'))):
            raise HTTPError(404, 'Inbox not found')
        headers = {name: self.headers[name] for name in CAPTURED_HEADERS if name in self.headers}
        external_id = headers.get('x-webhook-id') or None
        body_hash = sha256(raw_body)
        db.execute('BEGIN IMMEDIATE')
        if external_id is not None:
            previous = db.execute('SELECT id, body FROM events WHERE inbox_id = ? AND external_id = ?',
                                  (inbox_id, external_id)).fetchone()
            if previous is not None:
                if previous['body'] != body:
                    raise HTTPError(409, 'Idempotency key already used with a different body')
                return 200, {'id': previous['id'], 'duplicate': True}
        event_id = secrets.token_urlsafe(24)
        received_at = utc_now().isoformat(timespec='microseconds')
        db.execute('''INSERT INTO events (id, inbox_id, external_id, received_at, headers_json, body, body_sha256)
                      VALUES (?, ?, ?, ?, ?, ?, ?)''',
                   (event_id, inbox_id, external_id, received_at, json.dumps(headers), body, body_hash))
        return 201, {'id': event_id, 'duplicate': False}

    def list_events(self, db, body, raw_body, query, inbox_id):
        values = parse_qs(query, keep_blank_values=True).get('limit', ['20'])
        if len(values) != 1 or not re.fullmatch(r'[0-9]+', values[0]):
            raise HTTPError(400, 'Invalid limit')
        digits = values[0].lstrip('0') or '0'
        if len(digits) > 3 or not 1 <= int(digits) <= 100:
            raise HTTPError(400, 'Invalid limit')
        require_inbox(db, inbox_id)
        rows = db.execute(f'''SELECT {SUMMARY_COLUMNS} FROM events WHERE inbox_id = ?
                              ORDER BY received_at DESC, id DESC LIMIT ?''', (inbox_id, int(digits)))
        return 200, {'events': [dict(row) for row in rows]}

    def get_event(self, db, body, raw_body, query, event_id):
        row = db.execute(f'SELECT {SUMMARY_COLUMNS}, body, headers_json FROM events WHERE id = ?',
                         (event_id,)).fetchone()
        if row is None:
            raise HTTPError(404, 'Event not found')
        event = dict(row)
        event['headers'] = json.loads(event.pop('headers_json'))
        return 200, event

    def get_retention(self, db, body, raw_body, query, inbox_id):
        require_inbox(db, inbox_id)
        return 200, {'max_age_seconds': retention_policy(db, inbox_id)}

    def put_retention(self, db, body, raw_body, query, inbox_id):
        policy = json_object(body)
        if policy.keys() != {'max_age_seconds'}:
            raise HTTPError(400, 'Expected exactly max_age_seconds')
        age = policy['max_age_seconds']
        if age is not None and (type(age) is not int or not 1 <= age <= 2592000):
            raise HTTPError(400, 'Invalid max_age_seconds')
        db.execute('BEGIN IMMEDIATE')
        require_inbox(db, inbox_id)
        if age is None:
            db.execute('DELETE FROM retention_policies WHERE inbox_id = ?', (inbox_id,))
        else:
            db.execute('''INSERT INTO retention_policies (inbox_id, max_age_seconds) VALUES (?, ?)
                          ON CONFLICT(inbox_id) DO UPDATE SET max_age_seconds = excluded.max_age_seconds''',
                       (inbox_id, age))
        return 200, policy

    def expire(self, db, body, raw_body, query, inbox_id):
        options = json_object(body) if body else {}
        limit = options.get('limit', 100)
        if options.keys() - {'limit'} or type(limit) is not int or not 1 <= limit <= 100:
            raise HTTPError(400, 'Invalid expiration options')
        db.execute('BEGIN IMMEDIATE')
        require_inbox(db, inbox_id)
        age = retention_policy(db, inbox_id)
        if age is None:
            return 200, {'deleted': 0}
        cutoff = instant_key((utc_now() - timedelta(seconds=age)).isoformat())
        rows = db.execute('SELECT id, received_at FROM events WHERE inbox_id = ?', (inbox_id,))
        candidates = ((instant_key(row['received_at']), row['id']) for row in rows)
        oldest = heapq.nsmallest(limit, (candidate for candidate in candidates if candidate[0] <= cutoff))
        if not oldest:
            return 200, {'deleted': 0}
        placeholders = ', '.join('?' for _ in oldest)
        deleted = db.execute(f'DELETE FROM events WHERE inbox_id = ? AND id IN ({placeholders})',
                             (inbox_id, *(event_id for _, event_id in oldest))).rowcount
        return 200, {'deleted': deleted}


class Server(ThreadingHTTPServer):
    def handle_error(self, request, client_address):
        pass


def main():
    token = os.environ.get('PHOENIX_ADMIN_TOKEN')
    if not token:
        raise SystemExit('PHOENIX_ADMIN_TOKEN is required')
    db_path = os.environ.get('PHOENIX_DB_PATH', 'phoenix.sqlite')
    try:
        port = int(os.environ.get('PORT', '8787'))
        migrate(db_path)
        server = Server(('127.0.0.1', port), Handler)
    except (ValueError, OverflowError, OSError, sqlite3.Error):
        raise SystemExit('Phoenix startup failed') from None
    with server:
        server.db_path = db_path
        server.authorization = b'Bearer ' + os.fsencode(token)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass


if __name__ == '__main__':
    main()
