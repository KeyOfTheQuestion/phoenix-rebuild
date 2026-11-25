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
from urllib.parse import parse_qsl


MAX_BODY = 65536
MAX_AGE = 2592000
CAPTURED_HEADERS = ('content-type', 'x-webhook-id', 'x-event-type')
EVENT_FIELDS = 'id, inbox_id, external_id, received_at, body_sha256'
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
    conn = sqlite3.connect(path, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA foreign_keys=ON')
    return conn


def migrate(path):
    with closing(connect(path)) as conn, conn:
        conn.execute('BEGIN IMMEDIATE')
        version = conn.execute('PRAGMA user_version').fetchone()[0]
        if version == 2:
            return
        if version not in (0, 1):
            raise ValueError('Unsupported database schema version')
        for statement in SCHEMA:
            conn.execute(statement)
        conn.execute('PRAGMA user_version=2')


def utc_now():
    return datetime.now(timezone.utc)


def timestamp():
    return utc_now().isoformat(timespec='microseconds')


def instant_key(value):
    # Compare whole UTC seconds and the fractional digits separately so even
    # migrated timestamps with different fractional precision sort correctly.
    match = re.fullmatch(r'(.{19})(?:[.,]([0-9]+))?(Z|[+-][0-9]{2}:[0-9]{2})', value)
    if match is not None:
        whole, fraction, offset = match.groups()
        if offset == 'Z':
            offset = '+00:00'
        instant = datetime.fromisoformat(whole + offset).astimezone(timezone.utc)
        return instant, (fraction or '').rstrip('0')
    if value.endswith('Z'):
        value = value[:-1] + '+00:00'
    instant = datetime.fromisoformat(value)
    if instant.tzinfo is None:
        instant = instant.replace(tzinfo=timezone.utc)
    instant = instant.astimezone(timezone.utc)
    return instant.replace(microsecond=0), format(instant.microsecond, '06d').rstrip('0')


def sha256(value):
    return hashlib.sha256(value).hexdigest()


def reject_json_constant(value):
    raise ValueError('Invalid JSON constant')


def require_inbox(conn, inbox_id):
    if conn.execute('SELECT 1 FROM inboxes WHERE id = ?', (inbox_id,)).fetchone() is None:
        raise RequestError(404, 'Not found')


def integer_in_range(value, maximum):
    return type(value) is int and 1 <= value <= maximum


class Server(ThreadingHTTPServer):
    def __init__(self, address, db_path, admin_token):
        self.db_path = db_path
        self.admin_authorization = ('Bearer ' + admin_token).encode('utf-8')
        super().__init__(address, Handler)

    def handle_error(self, request, client_address):
        pass


class Handler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        pass

    def send_error(self, code, message=None, explain=None):
        self.respond(code, {'error': self.responses.get(code, ('Request failed',))[0]})

    def respond(self, status, data, allow=None):
        self.close_connection = True
        payload = json.dumps(data, ensure_ascii=True, separators=(',', ':')).encode('utf-8')
        # The default parser treats HTTP/0.9 as headerless. Always emit an
        # HTTP response with the JSON content type, even for those requests.
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
            return self.dispatch
        raise AttributeError(name)

    def authorize(self):
        values = self.headers.get_all('Authorization', [])
        if len(values) != 1 or not hmac.compare_digest(
            values[0].encode('latin-1'), self.server.admin_authorization
        ):
            raise RequestError(401, 'Unauthorized')

    def read_body(self, allow_missing):
        if 'Transfer-Encoding' in self.headers:
            raise RequestError(400, 'Transfer-Encoding is unsupported')
        lengths = self.headers.get_all('Content-Length', [])
        if not lengths and allow_missing:
            return b'', ''
        if len(lengths) != 1 or re.fullmatch(r'[0-9]+', lengths[0].strip(' \t')) is None:
            raise RequestError(400, 'Invalid Content-Length')
        digits = lengths[0].strip(' \t').lstrip('0') or '0'
        if len(digits) > 5 or int(digits) > MAX_BODY:
            raise RequestError(413, 'Body too large')
        length = int(digits)
        raw = self.rfile.read(length)
        if len(raw) != length:
            raise RequestError(400, 'Incomplete body')
        try:
            return raw, raw.decode('utf-8')
        except UnicodeDecodeError:
            raise RequestError(400, 'Invalid UTF-8') from None

    @staticmethod
    def json_object(text):
        try:
            value = json.loads(text, parse_constant=reject_json_constant)
        except (ValueError, RecursionError):
            raise RequestError(400, 'Invalid JSON') from None
        if not isinstance(value, dict):
            raise RequestError(400, 'Expected a JSON object')
        return value

    def dispatch(self):
        try:
            path, _, query = self.path.partition('?')
            if path.startswith('/api/'):
                self.authorize()
            route = self.route(path)
            if route is None:
                if self.command not in ('GET', 'POST', 'PUT'):
                    self.respond(405, {'error': 'Method not allowed'})
                    return
                raise RequestError(404, 'Not found')
            methods, action, args = route
            if self.command not in methods:
                self.respond(405, {'error': 'Method not allowed'}, methods)
                return
            with closing(connect(self.server.db_path)) as conn:
                if action == self.receive_hook:
                    self.authenticate_hook(conn, *args)
                raw, body = self.read_body(allow_missing=self.command != 'PUT')
                status, result = action(conn, raw, body, query, *args)
            self.respond(status, result)
        except RequestError as exc:
            self.respond(exc.status, {'error': str(exc)})
        except (BrokenPipeError, ConnectionResetError):
            self.close_connection = True
        except Exception:
            self.respond(500, {'error': 'Internal server error'})

    def route(self, path):
        if path == '/health':
            return ('GET',), self.health, ()
        if path == '/api/inboxes':
            if self.command == 'POST':
                return ('GET', 'POST'), self.create_inbox, ()
            return ('GET', 'POST'), self.list_inboxes, ()
        match = re.fullmatch(r'/api/inboxes/([^/]+)/(events|retention|expire)', path)
        if match is not None:
            inbox_id, endpoint = match.groups()
            if endpoint == 'events':
                return ('GET',), self.list_events, (inbox_id,)
            if endpoint == 'expire':
                return ('POST',), self.expire, (inbox_id,)
            if self.command == 'PUT':
                return ('GET', 'PUT'), self.put_retention, (inbox_id,)
            return ('GET', 'PUT'), self.get_retention, (inbox_id,)
        match = re.fullmatch(r'/api/events/([^/]+)', path)
        if match is not None:
            return ('GET',), self.get_event, match.groups()
        match = re.fullmatch(r'/hooks/([^/]+)/([^/]+)', path)
        if match is not None:
            return ('POST',), self.receive_hook, match.groups()
        return None

    def health(self, conn, raw, body, query):
        return 200, {'status': 'ok', 'schema_version': 2}

    def create_inbox(self, conn, raw, body, query):
        name = self.json_object(body).get('name')
        if not isinstance(name, str) or not 1 <= len(name.strip()) <= 80:
            raise RequestError(400, 'Invalid name')
        name = name.strip()
        try:
            name.encode('utf-8')
        except UnicodeEncodeError:
            raise RequestError(400, 'Invalid name') from None
        inbox_id = secrets.token_urlsafe(18)
        token = secrets.token_urlsafe(32)
        created_at = timestamp()
        with conn:
            conn.execute(
                'INSERT INTO inboxes (id, name, token_sha256, created_at) VALUES (?, ?, ?, ?)',
                (inbox_id, name, sha256(token.encode('ascii')), created_at),
            )
        return 201, {'id': inbox_id, 'name': name, 'created_at': created_at, 'token': token}

    def list_inboxes(self, conn, raw, body, query):
        rows = conn.execute('SELECT id, name, created_at FROM inboxes ORDER BY created_at DESC, id DESC')
        return 200, {'inboxes': [dict(row) for row in rows]}

    def authenticate_hook(self, conn, inbox_id, token):
        row = conn.execute('SELECT token_sha256 FROM inboxes WHERE id = ?', (inbox_id,)).fetchone()
        digest = sha256(token.encode('utf-8'))
        if row is None or not hmac.compare_digest(row['token_sha256'], digest):
            raise RequestError(404, 'Not found')

    def receive_hook(self, conn, raw, body, query, inbox_id, token):
        headers = {name: self.headers[name] for name in CAPTURED_HEADERS if name in self.headers}
        external_id = headers.get('x-webhook-id') or None
        with conn:
            conn.execute('BEGIN IMMEDIATE')
            if external_id is not None:
                row = conn.execute(
                    'SELECT id, body FROM events WHERE inbox_id = ? AND external_id = ?',
                    (inbox_id, external_id),
                ).fetchone()
                if row is not None:
                    if row['body'] != body:
                        raise RequestError(409, 'Webhook ID already used with a different body')
                    return 200, {'id': row['id'], 'duplicate': True}
            event_id = secrets.token_urlsafe(18)
            conn.execute(
                '''INSERT INTO events
                   (id, inbox_id, external_id, received_at, headers_json, body, body_sha256)
                   VALUES (?, ?, ?, ?, ?, ?, ?)''',
                (event_id, inbox_id, external_id, timestamp(), json.dumps(headers), body, sha256(raw)),
            )
        return 201, {'id': event_id, 'duplicate': False}

    def list_events(self, conn, raw, body, query, inbox_id):
        require_inbox(conn, inbox_id)
        limits = [value for key, value in parse_qsl(query, keep_blank_values=True) if key == 'limit']
        limit = 20
        if limits:
            digits = limits[0].lstrip('0')
            if len(limits) != 1 or re.fullmatch(r'[0-9]+', limits[0]) is None or not 1 <= len(digits) <= 3:
                raise RequestError(400, 'Invalid limit')
            limit = int(digits)
            if limit > 100:
                raise RequestError(400, 'Invalid limit')
        rows = conn.execute(
            f'SELECT {EVENT_FIELDS} FROM events WHERE inbox_id = ? ORDER BY received_at DESC, id DESC LIMIT ?',
            (inbox_id, limit),
        )
        return 200, {'events': [dict(row) for row in rows]}

    def get_event(self, conn, raw, body, query, event_id):
        row = conn.execute(
            f'SELECT {EVENT_FIELDS}, body, headers_json FROM events WHERE id = ?', (event_id,)
        ).fetchone()
        if row is None:
            raise RequestError(404, 'Not found')
        result = dict(row)
        result['headers'] = json.loads(result.pop('headers_json'))
        return 200, result

    @staticmethod
    def read_policy(conn, inbox_id):
        row = conn.execute(
            'SELECT max_age_seconds FROM retention_policies WHERE inbox_id = ?', (inbox_id,)
        ).fetchone()
        return None if row is None else row['max_age_seconds']

    def get_retention(self, conn, raw, body, query, inbox_id):
        require_inbox(conn, inbox_id)
        return 200, {'max_age_seconds': self.read_policy(conn, inbox_id)}

    def put_retention(self, conn, raw, body, query, inbox_id):
        require_inbox(conn, inbox_id)
        value = self.json_object(body)
        if value.keys() != {'max_age_seconds'}:
            raise RequestError(400, 'Expected max_age_seconds')
        age = value['max_age_seconds']
        if age is not None and not integer_in_range(age, MAX_AGE):
            raise RequestError(400, 'Invalid max_age_seconds')
        with conn:
            if age is None:
                conn.execute('DELETE FROM retention_policies WHERE inbox_id = ?', (inbox_id,))
            else:
                conn.execute(
                    '''INSERT INTO retention_policies (inbox_id, max_age_seconds) VALUES (?, ?)
                       ON CONFLICT(inbox_id) DO UPDATE SET max_age_seconds = excluded.max_age_seconds''',
                    (inbox_id, age),
                )
        return 200, {'max_age_seconds': age}

    def expire(self, conn, raw, body, query, inbox_id):
        require_inbox(conn, inbox_id)
        value = self.json_object(body) if body else {}
        limit = value.get('limit', 100)
        if value.keys() - {'limit'} or not integer_in_range(limit, 100):
            raise RequestError(400, 'Invalid expiry options')
        deleted = 0
        with conn:
            conn.execute('BEGIN IMMEDIATE')
            age = self.read_policy(conn, inbox_id)
            if age is not None:
                cutoff = utc_now() - timedelta(seconds=age)
                cutoff_key = instant_key(cutoff.isoformat())
                rows = conn.execute('SELECT id, received_at FROM events WHERE inbox_id = ?', (inbox_id,))
                eligible = []
                for row in rows:
                    received = instant_key(row['received_at'])
                    if received <= cutoff_key:
                        eligible.append((received, row['id']))
                eligible.sort()
                ids = [event_id for _, event_id in eligible[:limit]]
                if ids:
                    placeholders = ', '.join('?' for _ in ids)
                    deleted = conn.execute(
                        f'DELETE FROM events WHERE inbox_id = ? AND id IN ({placeholders})',
                        (inbox_id, *ids),
                    ).rowcount
        return 200, {'deleted': deleted}


def main():
    admin_token = os.environ.get('PHOENIX_ADMIN_TOKEN')
    if not admin_token:
        raise SystemExit('PHOENIX_ADMIN_TOKEN is required')
    db_path = os.environ.get('PHOENIX_DB_PATH', 'phoenix.sqlite')
    try:
        port = int(os.environ.get('PORT', '8787'))
        if not 0 <= port <= 65535:
            raise ValueError('Invalid port')
        migrate(db_path)
        with Server(('127.0.0.1', port), db_path, admin_token) as server:
            server.serve_forever()
    except KeyboardInterrupt:
        pass
    except (ValueError, OSError, sqlite3.Error):
        raise SystemExit('Unable to start Phoenix') from None


if __name__ == '__main__':
    main()
