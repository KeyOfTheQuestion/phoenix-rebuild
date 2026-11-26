import hashlib
import heapq
import hmac
import json
import os
import re
import secrets
import sqlite3
import sys
from contextlib import closing
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qsl


MAX_BODY = 65536
MAX_AGE = 2592000
CAPTURED_HEADERS = ('content-type', 'x-webhook-id', 'x-event-type')
EVENT_COLUMNS = 'id, inbox_id, external_id, received_at, body_sha256'
INBOX_EVENTS = re.compile(r'/api/inboxes/([^/]+)/events')
INBOX_RETENTION = re.compile(r'/api/inboxes/([^/]+)/retention')
INBOX_EXPIRE = re.compile(r'/api/inboxes/([^/]+)/expire')
EVENT_DETAIL = re.compile(r'/api/events/([^/]+)')
HOOK = re.compile(r'/hooks/([^/]+)/([^/]+)')

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


class RequestError(Exception):
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
        for statement in SCHEMA:
            db.execute(statement)
        db.execute('PRAGMA user_version=2')


def utc_now():
    return datetime.now(timezone.utc)


def parse_instant(value):
    if value.endswith('Z'):
        value = value[:-1] + '+00:00'
    instant = datetime.fromisoformat(value)
    if instant.tzinfo is None:
        return instant.replace(tzinfo=timezone.utc)
    return instant.astimezone(timezone.utc)


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def strict_object(pairs):
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
        value = json.loads(body, object_pairs_hook=strict_object, parse_constant=reject_constant)
    except (ValueError, RecursionError):
        raise RequestError(400, 'Invalid JSON') from None
    if not isinstance(value, dict):
        raise RequestError(400, 'Expected a JSON object')
    return value


def list_limit(query):
    values = [value for key, value in parse_qsl(query, keep_blank_values=True) if key == 'limit']
    if not values:
        return 20
    if len(values) == 1 and re.fullmatch(r'[0-9]+', values[0]):
        digits = values[0].lstrip('0')
        if 1 <= len(digits) <= 3 and int(digits) <= 100:
            return int(digits)
    raise RequestError(400, 'Invalid limit')


def require_inbox(db, inbox_id):
    if db.execute('SELECT 1 FROM inboxes WHERE id = ?', (inbox_id,)).fetchone() is None:
        raise RequestError(404, 'Inbox not found')


def retention_age(db, inbox_id):
    row = db.execute('SELECT max_age_seconds FROM retention_policies WHERE inbox_id = ?', (inbox_id,)).fetchone()
    return None if row is None else row['max_age_seconds']


class Handler(BaseHTTPRequestHandler):
    # One request per connection, including when a request body is rejected.
    protocol_version = 'HTTP/1.0'

    def log_message(self, format, *args):
        pass

    def send_error(self, code, message=None, explain=None):
        self.close_connection = True
        self.respond(code, {'error': self.responses.get(code, ('HTTP error',))[0]})

    def respond(self, status, value, allow=None):
        payload = json.dumps(value, ensure_ascii=True, separators=(',', ':')).encode('utf-8')
        if self.request_version == 'HTTP/0.9':
            self.request_version = 'HTTP/1.0'
        self.send_response(status)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(payload)))
        self.send_header('Connection', 'close')
        if allow is not None:
            self.send_header('Allow', ', '.join(allow))
        self.end_headers()
        if self.command != 'HEAD':
            self.wfile.write(payload)

    def __getattr__(self, name):
        if name.startswith('do_'):
            return self.handle_request
        raise AttributeError(name)

    def read_body(self, allow_missing):
        if self.headers.get_all('Transfer-Encoding') is not None:
            raise RequestError(400, 'Transfer-Encoding is unsupported')
        lengths = self.headers.get_all('Content-Length', [])
        if not lengths and allow_missing:
            return ''
        if len(lengths) != 1 or not re.fullmatch(r'[0-9]+', lengths[0].strip(' \t')):
            raise RequestError(400, 'Invalid or missing Content-Length')
        digits = lengths[0].strip(' \t').lstrip('0') or '0'
        if len(digits) > 5 or int(digits) > MAX_BODY:
            raise RequestError(413, 'Request body too large')
        length = int(digits)
        data = self.rfile.read(length)
        if len(data) != length:
            raise RequestError(400, 'Incomplete request body')
        try:
            return data.decode('utf-8')
        except UnicodeDecodeError:
            raise RequestError(400, 'Invalid UTF-8') from None

    def handle_request(self):
        self.close_connection = True
        try:
            path, _, query = self.path.partition('?')
            if path.startswith('/api/'):
                credentials = self.headers.get_all('Authorization', [])
                if len(credentials) != 1 or not hmac.compare_digest(
                    credentials[0].encode('latin-1'), self.server.admin_authorization
                ):
                    raise RequestError(401, 'Unauthorized')

            method = self.command
            routes = (
                ('/health', {'GET': self.health}),
                ('/api/inboxes', {'GET': self.inboxes, 'POST': self.create_inbox}),
                (INBOX_EVENTS, {'GET': self.list_events}),
                (INBOX_RETENTION, {'GET': self.get_retention, 'PUT': self.put_retention}),
                (INBOX_EXPIRE, {'POST': self.expire}),
                (EVENT_DETAIL, {'GET': self.event_detail}),
                (HOOK, {'POST': self.receive_hook}),
            )
            for pattern, handlers in routes:
                if isinstance(pattern, str):
                    if path != pattern:
                        continue
                    args = ()
                else:
                    match = pattern.fullmatch(path)
                    if match is None:
                        continue
                    args = match.groups()
                if method not in handlers:
                    self.respond(405, {'error': 'Method not allowed'}, allow=handlers)
                    return
                bodyless = method == 'GET' or pattern in (HOOK, INBOX_EXPIRE)
                body = self.read_body(allow_missing=bodyless)
                if pattern == '/health':
                    status, result = self.health()
                else:
                    with closing(connect(self.server.db_path)) as db, db:
                        status, result = handlers[method](db, body, query, *args)
                break
            else:
                if method not in ('GET', 'POST', 'PUT'):
                    raise RequestError(405, 'Method not allowed')
                raise RequestError(404, 'Not found')
        except RequestError as error:
            status, result = error.status, {'error': str(error)}
        except Exception:
            status, result = 500, {'error': 'Internal server error'}
        self.respond(status, result)

    def health(self):
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
        inbox_id = secrets.token_hex(16)
        token = secrets.token_urlsafe(32)
        created_at = utc_now().isoformat(timespec='microseconds')
        db.execute(
            'INSERT INTO inboxes (id, name, token_sha256, created_at) VALUES (?, ?, ?, ?)',
            (inbox_id, name, sha256(token.encode('ascii')), created_at),
        )
        return 201, {'id': inbox_id, 'name': name, 'created_at': created_at, 'token': token}

    def inboxes(self, db, body, query):
        rows = db.execute('SELECT id, name, created_at FROM inboxes ORDER BY created_at DESC, id DESC')
        return 200, {'inboxes': [dict(row) for row in rows]}

    def receive_hook(self, db, body, query, inbox_id, token):
        db.execute('BEGIN IMMEDIATE')
        inbox = db.execute('SELECT token_sha256 FROM inboxes WHERE id = ?', (inbox_id,)).fetchone()
        if inbox is None or not hmac.compare_digest(inbox['token_sha256'], sha256(token.encode('utf-8'))):
            raise RequestError(404, 'Inbox not found')
        headers = {name: self.headers[name] for name in CAPTURED_HEADERS if name in self.headers}
        external_id = headers.get('x-webhook-id') or None
        if external_id is not None:
            existing = db.execute(
                'SELECT id, body FROM events WHERE inbox_id = ? AND external_id = ?',
                (inbox_id, external_id),
            ).fetchone()
            if existing is not None:
                if existing['body'] != body:
                    raise RequestError(409, 'Webhook ID already used with a different body')
                return 200, {'id': existing['id'], 'duplicate': True}
        event_id = secrets.token_hex(16)
        db.execute(
            '''INSERT INTO events
               (id, inbox_id, external_id, received_at, headers_json, body, body_sha256)
               VALUES (?, ?, ?, ?, ?, ?, ?)''',
            (event_id, inbox_id, external_id, utc_now().isoformat(timespec='microseconds'),
             json.dumps(headers), body, sha256(body.encode('utf-8'))),
        )
        return 201, {'id': event_id, 'duplicate': False}

    def list_events(self, db, body, query, inbox_id):
        require_inbox(db, inbox_id)
        limit = list_limit(query)
        rows = db.execute(
            f'SELECT {EVENT_COLUMNS} FROM events WHERE inbox_id = ? ORDER BY received_at DESC, id DESC LIMIT ?',
            (inbox_id, limit),
        )
        return 200, {'events': [dict(row) for row in rows]}

    def event_detail(self, db, body, query, event_id):
        row = db.execute(
            f'SELECT {EVENT_COLUMNS}, body, headers_json FROM events WHERE id = ?', (event_id,)
        ).fetchone()
        if row is None:
            raise RequestError(404, 'Event not found')
        result = dict(row)
        result['headers'] = json.loads(result.pop('headers_json'))
        return 200, result

    def get_retention(self, db, body, query, inbox_id):
        require_inbox(db, inbox_id)
        return 200, {'max_age_seconds': retention_age(db, inbox_id)}

    def put_retention(self, db, body, query, inbox_id):
        policy = json_object(body)
        if policy.keys() != {'max_age_seconds'}:
            raise RequestError(400, 'Expected only max_age_seconds')
        age = policy['max_age_seconds']
        if age is not None and (type(age) is not int or not 1 <= age <= MAX_AGE):
            raise RequestError(400, 'Invalid max_age_seconds')
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

    def expire(self, db, body, query, inbox_id):
        options = json_object(body) if body else {}
        limit = options.get('limit', 100)
        if options.keys() - {'limit'} or type(limit) is not int or not 1 <= limit <= 100:
            raise RequestError(400, 'Invalid expiration options')
        db.execute('BEGIN IMMEDIATE')
        require_inbox(db, inbox_id)
        age = retention_age(db, inbox_id)
        if age is None:
            return 200, {'deleted': 0}
        cutoff = utc_now() - timedelta(seconds=age)
        rows = db.execute('SELECT id, received_at FROM events WHERE inbox_id = ?', (inbox_id,))

        def eligible():
            for row in rows:
                instant = parse_instant(row['received_at'])
                if instant <= cutoff:
                    yield instant, row['id']

        oldest = heapq.nsmallest(limit, eligible())
        result = db.executemany(
            'DELETE FROM events WHERE inbox_id = ? AND id = ?',
            ((inbox_id, event_id) for _, event_id in oldest),
        )
        return 200, {'deleted': result.rowcount}


class Server(ThreadingHTTPServer):
    def handle_error(self, request, client_address):
        pass


def main():
    token = os.environ.get('PHOENIX_ADMIN_TOKEN')
    if not token:
        sys.exit('PHOENIX_ADMIN_TOKEN is required')
    path = os.environ.get('PHOENIX_DB_PATH', 'phoenix.sqlite')
    try:
        port = int(os.environ.get('PORT', '8787'))
        if not 0 <= port <= 65535:
            raise ValueError('Invalid port')
        migrate(path)
        server = Server(('127.0.0.1', port), Handler)
    except (ValueError, OSError, sqlite3.Error):
        sys.exit('Phoenix startup failed')
    with server:
        server.db_path = path
        server.admin_authorization = b'Bearer ' + token.encode('utf-8')
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass


if __name__ == '__main__':
    main()
