import hashlib
import heapq
import hmac
import json
import os
import re
import secrets
import sqlite3
from contextlib import closing, contextmanager
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qsl, unquote


MAX_BODY = 65_536
CAPTURED_HEADERS = ('content-type', 'x-webhook-id', 'x-event-type')
EVENT_FIELDS = 'id, inbox_id, external_id, received_at, body_sha256'
ROUTES = (
    (re.compile(r'/health'), {'GET': 'health'}),
    (re.compile(r'/api/inboxes'), {'GET': 'list_inboxes', 'POST': 'create_inbox'}),
    (re.compile(r'/hooks/([^/]+)/([^/]+)'), {'POST': 'receive_hook'}),
    (re.compile(r'/api/inboxes/([^/]+)/events'), {'GET': 'list_events'}),
    (re.compile(r'/api/events/([^/]+)'), {'GET': 'get_event'}),
    (re.compile(r'/api/inboxes/([^/]+)/retention'), {'GET': 'get_policy', 'PUT': 'put_policy'}),
    (re.compile(r'/api/inboxes/([^/]+)/expire'), {'POST': 'expire_events'}),
)


class RequestError(Exception):
    def __init__(self, status, message):
        super().__init__(message)
        self.status = status


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


def utc_now():
    return datetime.now(timezone.utc)


def sha256(text):
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


def bounded_decimal(text, maximum, status, message):
    if re.fullmatch(r'[0-9]+', text) is None:
        raise RequestError(400, message)
    digits = text.lstrip('0') or '0'
    ceiling = str(maximum)
    if len(digits) > len(ceiling) or (len(digits) == len(ceiling) and digits > ceiling):
        raise RequestError(status, message)
    return int(digits)


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
        result = json.loads(body, object_pairs_hook=unique_object, parse_constant=reject_constant)
    except (ValueError, RecursionError):
        raise RequestError(400, 'Invalid JSON') from None
    if not isinstance(result, dict):
        raise RequestError(400, 'Expected a JSON object')
    return result


def require_inbox(db, inbox_id):
    if db.execute('SELECT 1 FROM inboxes WHERE id = ?', (inbox_id,)).fetchone() is None:
        raise RequestError(404, 'Inbox not found')


def read_policy(db, inbox_id):
    row = db.execute('''SELECT p.max_age_seconds FROM inboxes AS i
        LEFT JOIN retention_policies AS p ON p.inbox_id = i.id
        WHERE i.id = ?''', (inbox_id,)).fetchone()
    if row is None:
        raise RequestError(404, 'Inbox not found')
    return row['max_age_seconds']


def instant_key(timestamp):
    # Keep fractional precision and compare offsets by their UTC instant.
    text = timestamp.replace(',', '.')
    if text.endswith('Z'):
        text = text[:-1] + '+00:00'
    match = re.search(r'\.([0-9]+)', text)
    fraction = ''
    if match is not None:
        fraction = match[1].rstrip('0')
        text = text[:match.start()] + text[match.end():]
    instant = datetime.fromisoformat(text)
    if instant.tzinfo is None:
        instant = instant.replace(tzinfo=timezone.utc)
    return instant.astimezone(timezone.utc), fraction


def eligible_events(rows, cutoff):
    for row in rows:
        instant = instant_key(row['received_at'])
        if instant <= cutoff:
            yield instant, row['id']


class Handler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        pass

    def __getattr__(self, name):
        if name.startswith('do_'):
            return self.dispatch
        raise AttributeError(name)

    def respond(self, status, payload, allow=None):
        body = json.dumps(payload, ensure_ascii=True, separators=(',', ':')).encode('utf-8')
        self.close_connection = True
        self.send_response(status)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Connection', 'close')
        if allow is not None:
            self.send_header('Allow', allow)
        self.end_headers()
        if self.command != 'HEAD':
            self.wfile.write(body)

    def send_error(self, code, message=None, explain=None):
        self.respond(code, {'error': self.responses.get(code, ('HTTP error',))[0]})

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
        length = bounded_decimal(lengths[0].strip(' \t'), MAX_BODY, 413, 'Invalid or oversized body length')
        raw = self.rfile.read(length)
        if len(raw) != length:
            raise RequestError(400, 'Incomplete body')
        try:
            return raw.decode('utf-8')
        except UnicodeDecodeError:
            raise RequestError(400, 'Body must be valid UTF-8') from None

    def dispatch(self):
        try:
            self.route_request()
        except RequestError as error:
            self.respond(error.status, {'error': str(error)})
        except (ConnectionError, TimeoutError):
            self.close_connection = True
        except Exception:
            self.respond(500, {'error': 'Internal server error'})

    def route_request(self):
        path, _, query = self.path.partition('?')
        if path.startswith('/api/'):
            auth = self.headers.get_all('Authorization', [])
            if len(auth) != 1 or not hmac.compare_digest(auth[0].encode('latin-1'), self.server.admin_auth):
                raise RequestError(401, 'Unauthorized')
        if self.command not in ('GET', 'POST', 'PUT'):
            for pattern, methods in ROUTES:
                if pattern.fullmatch(path):
                    self.respond(405, {'error': 'Method not allowed'}, ', '.join(methods))
                    return
            raise RequestError(405, 'Method not allowed')
        for pattern, methods in ROUTES:
            match = pattern.fullmatch(path)
            if match is None:
                continue
            operation = methods.get(self.command)
            if operation is None:
                self.respond(405, {'error': 'Method not allowed'}, ', '.join(methods))
                return
            body = self.read_body()
            arguments = tuple(unquote(value) for value in match.groups())
            with closing(connect(self.server.db_path)) as db:
                status, payload = getattr(self, operation)(db, body, query, *arguments)
            self.respond(status, payload)
            return
        raise RequestError(404, 'Resource not found')

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
        created_at = utc_now().isoformat(timespec='microseconds')
        with write_transaction(db):
            db.execute('''INSERT INTO inboxes (id, name, token_sha256, created_at)
                VALUES (?, ?, ?, ?)''', (inbox_id, name, sha256(token), created_at))
        return 201, {'id': inbox_id, 'name': name, 'created_at': created_at, 'token': token}

    def list_inboxes(self, db, body, query):
        rows = db.execute('SELECT id, name, created_at FROM inboxes ORDER BY created_at DESC, id DESC')
        return 200, {'inboxes': [dict(row) for row in rows]}

    def receive_hook(self, db, body, query, inbox_id, token):
        inbox = db.execute('SELECT token_sha256 FROM inboxes WHERE id = ?', (inbox_id,)).fetchone()
        if inbox is None or not hmac.compare_digest(inbox['token_sha256'], sha256(token)):
            raise RequestError(404, 'Inbox not found')
        headers = {name: self.headers[name] for name in CAPTURED_HEADERS if name in self.headers}
        external_id = headers.get('x-webhook-id') or None
        body_digest = sha256(body)
        with write_transaction(db):
            if external_id is not None:
                existing = db.execute('SELECT id, body FROM events WHERE inbox_id = ? AND external_id = ?',
                                      (inbox_id, external_id)).fetchone()
                if existing is not None:
                    if existing['body'] != body:
                        raise RequestError(409, 'Webhook ID already used with a different body')
                    return 200, {'id': existing['id'], 'duplicate': True}
            event_id = secrets.token_urlsafe(24)
            received_at = utc_now().isoformat(timespec='microseconds')
            db.execute('''INSERT INTO events
                (id, inbox_id, external_id, received_at, headers_json, body, body_sha256)
                VALUES (?, ?, ?, ?, ?, ?, ?)''',
                (event_id, inbox_id, external_id, received_at, json.dumps(headers), body, body_digest))
        return 201, {'id': event_id, 'duplicate': False}

    def list_events(self, db, body, query, inbox_id):
        limits = [value for key, value in parse_qsl(query, keep_blank_values=True) if key == 'limit']
        if len(limits) > 1:
            raise RequestError(400, 'Invalid limit')
        limit = bounded_decimal(limits[0], 100, 400, 'Invalid limit') if limits else 20
        if limit == 0:
            raise RequestError(400, 'Invalid limit')
        require_inbox(db, inbox_id)
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

    def get_policy(self, db, body, query, inbox_id):
        return 200, {'max_age_seconds': read_policy(db, inbox_id)}

    def put_policy(self, db, body, query, inbox_id):
        policy = json_object(body)
        if policy.keys() != {'max_age_seconds'}:
            raise RequestError(400, 'Expected only max_age_seconds')
        age = policy['max_age_seconds']
        if age is not None and (type(age) is not int or not 1 <= age <= 2_592_000):
            raise RequestError(400, 'Invalid max_age_seconds')
        with write_transaction(db):
            require_inbox(db, inbox_id)
            if age is None:
                db.execute('DELETE FROM retention_policies WHERE inbox_id = ?', (inbox_id,))
            else:
                db.execute('''INSERT INTO retention_policies (inbox_id, max_age_seconds)
                    VALUES (?, ?) ON CONFLICT(inbox_id) DO UPDATE
                    SET max_age_seconds = excluded.max_age_seconds''', (inbox_id, age))
        return 200, policy

    def expire_events(self, db, body, query, inbox_id):
        options = json_object(body) if body else {}
        limit = options.get('limit', 100)
        if options.keys() - {'limit'} or type(limit) is not int or not 1 <= limit <= 100:
            raise RequestError(400, 'Invalid expiration options')
        deleted = 0
        with write_transaction(db):
            age = read_policy(db, inbox_id)
            if age is not None:
                cutoff = instant_key((utc_now() - timedelta(seconds=age)).isoformat())
                rows = db.execute('SELECT id, received_at FROM events WHERE inbox_id = ?', (inbox_id,))
                batch = heapq.nsmallest(limit, eligible_events(rows, cutoff))
                if batch:
                    cursor = db.executemany('DELETE FROM events WHERE inbox_id = ? AND id = ?',
                                            ((inbox_id, event_id) for _, event_id in batch))
                    deleted = cursor.rowcount
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
        with Server(('127.0.0.1', port), Handler) as server:
            server.db_path = db_path
            server.admin_auth = ('Bearer ' + token).encode('utf-8')
            server.serve_forever()
    except KeyboardInterrupt:
        pass
    except RuntimeError as error:
        raise SystemExit(str(error)) from None
    except (OSError, ValueError, OverflowError, sqlite3.Error):
        raise SystemExit('Unable to start Phoenix') from None


if __name__ == '__main__':
    main()
