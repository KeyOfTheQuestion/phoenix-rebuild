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
from urllib.parse import parse_qsl, unquote


MAX_BODY = 65536
MAX_AGE = 2592000
DECIMAL = re.compile(r'[0-9]+')
SUMMARY = 'id, inbox_id, external_id, received_at, body_sha256'
CAPTURED_HEADERS = ('content-type', 'x-webhook-id', 'x-event-type')


class RequestError(Exception):
    def __init__(self, status, message):
        super().__init__(message)
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


def utc_now():
    return datetime.now(timezone.utc)


def timestamp():
    return utc_now().isoformat(timespec='microseconds')


def received_instant(value):
    if value.endswith('Z'):
        value = value[:-1] + '+00:00'
    return datetime.fromisoformat(value).astimezone(timezone.utc)


def digest(data):
    return hashlib.sha256(data).hexdigest()


def unique_object(pairs):
    result = {}
    for name, value in pairs:
        if name in result:
            raise ValueError('Duplicate JSON key')
        result[name] = value
    return result


def invalid_constant(value):
    raise ValueError('Invalid JSON constant')


def json_object(text):
    try:
        obj = json.loads(text, object_pairs_hook=unique_object,
                         parse_constant=invalid_constant)
    except (ValueError, RecursionError):
        raise RequestError(400, 'Invalid JSON') from None
    if not isinstance(obj, dict):
        raise RequestError(400, 'Expected a JSON object')
    return obj


def list_limit(query):
    values = [value for name, value in parse_qsl(query, keep_blank_values=True)
              if name == 'limit']
    if not values:
        return 20
    if len(values) != 1 or not DECIMAL.fullmatch(values[0]):
        raise RequestError(400, 'Invalid limit')
    digits = values[0].lstrip('0')
    if not digits or len(digits) > 3 or int(digits) > 100:
        raise RequestError(400, 'Invalid limit')
    return int(digits)


def get_inbox(db, inbox_id):
    row = db.execute('SELECT token_sha256 FROM inboxes WHERE id=?', (inbox_id,)).fetchone()
    if row is None:
        raise RequestError(404, 'Inbox not found')
    return row


def get_policy(db, inbox_id):
    row = db.execute('SELECT max_age_seconds FROM retention_policies WHERE inbox_id=?',
                     (inbox_id,)).fetchone()
    return None if row is None else row['max_age_seconds']


def create_inbox(db, body):
    name = json_object(body).get('name')
    if not isinstance(name, str) or not 1 <= len(name.strip()) <= 80:
        raise RequestError(400, 'Invalid inbox name')
    name = name.strip()
    try:
        name.encode('utf-8')
    except UnicodeEncodeError:
        raise RequestError(400, 'Invalid inbox name') from None
    inbox_id = secrets.token_urlsafe(18)
    token = secrets.token_urlsafe(32)
    created = timestamp()
    with db:
        db.execute('INSERT INTO inboxes (id, name, token_sha256, created_at) VALUES (?, ?, ?, ?)',
                   (inbox_id, name, digest(token.encode('ascii')), created))
    return 201, {'id': inbox_id, 'name': name, 'created_at': created, 'token': token}


def receive_hook(db, inbox_id, token, body, headers):
    inbox = get_inbox(db, inbox_id)
    if not hmac.compare_digest(inbox['token_sha256'], digest(token.encode('utf-8'))):
        raise RequestError(404, 'Inbox not found')
    external_id = headers.get('x-webhook-id') or None
    with db:
        db.execute('BEGIN IMMEDIATE')
        if external_id is not None:
            prior = db.execute('SELECT id, body FROM events WHERE inbox_id=? AND external_id=?',
                               (inbox_id, external_id)).fetchone()
            if prior is not None:
                if prior['body'] != body:
                    raise RequestError(409, 'Webhook ID already used with a different body')
                return 200, {'id': prior['id'], 'duplicate': True}
        event_id = secrets.token_urlsafe(18)
        db.execute('''INSERT INTO events
            (id, inbox_id, external_id, received_at, headers_json, body, body_sha256)
            VALUES (?, ?, ?, ?, ?, ?, ?)''',
                   (event_id, inbox_id, external_id, timestamp(), json.dumps(headers),
                    body, digest(body.encode('utf-8'))))
    return 201, {'id': event_id, 'duplicate': False}


def retention(db, inbox_id, method, body):
    if method == 'GET':
        get_inbox(db, inbox_id)
        return 200, {'max_age_seconds': get_policy(db, inbox_id)}
    with db:
        db.execute('BEGIN IMMEDIATE')
        get_inbox(db, inbox_id)
        obj = json_object(body)
        if obj.keys() != {'max_age_seconds'}:
            raise RequestError(400, 'Expected only max_age_seconds')
        age = obj['max_age_seconds']
        if age is not None and (type(age) is not int or not 1 <= age <= MAX_AGE):
            raise RequestError(400, 'Invalid max_age_seconds')
        if age is None:
            db.execute('DELETE FROM retention_policies WHERE inbox_id=?', (inbox_id,))
        else:
            db.execute('''INSERT INTO retention_policies (inbox_id, max_age_seconds)
                VALUES (?, ?) ON CONFLICT(inbox_id) DO UPDATE
                SET max_age_seconds=excluded.max_age_seconds''', (inbox_id, age))
    return 200, {'max_age_seconds': age}


def expire(db, inbox_id, body):
    with db:
        db.execute('BEGIN IMMEDIATE')
        get_inbox(db, inbox_id)
        obj = json_object(body) if body else {}
        limit = obj.get('limit', 100)
        if obj.keys() - {'limit'} or type(limit) is not int or not 1 <= limit <= 100:
            raise RequestError(400, 'Invalid expiration limit')
        age = get_policy(db, inbox_id)
        if age is None:
            return 200, {'deleted': 0}
        cutoff = utc_now() - timedelta(seconds=age)
        rows = db.execute('SELECT id, received_at FROM events WHERE inbox_id=?', (inbox_id,))
        instants = ((received_instant(row['received_at']), row['id']) for row in rows)
        oldest = heapq.nsmallest(limit, (entry for entry in instants if entry[0] <= cutoff))
        deleted = db.executemany('DELETE FROM events WHERE inbox_id=? AND id=?',
                                 ((inbox_id, event_id) for _, event_id in oldest)).rowcount
    return 200, {'deleted': deleted}


class Handler(BaseHTTPRequestHandler):
    def setup(self):
        self.request.settimeout(15)
        super().setup()

    def log_message(self, format, *args):
        pass

    def __getattr__(self, name):
        if name.startswith('do_'):
            return self.dispatch
        raise AttributeError(name)

    def reply(self, status, payload, allow=None):
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

    def send_error(self, code, message=None, explain=None):
        self.reply(code, {'error': self.responses.get(code, ('HTTP error',))[0]})

    def read_body(self, require_length):
        if self.headers.get_all('Transfer-Encoding') is not None:
            raise RequestError(400, 'Transfer-Encoding is unsupported')
        lengths = self.headers.get_all('Content-Length', [])
        if not lengths and not require_length:
            return ''
        if len(lengths) != 1 or not DECIMAL.fullmatch(lengths[0].strip(' \t')):
            raise RequestError(400, 'Invalid Content-Length')
        digits = lengths[0].strip(' \t').lstrip('0') or '0'
        if len(digits) > 5 or int(digits) > MAX_BODY:
            raise RequestError(413, 'Body too large')
        length = int(digits)
        try:
            raw = self.rfile.read(length)
        except OSError:
            raise RequestError(400, 'Incomplete body') from None
        if len(raw) != length:
            raise RequestError(400, 'Incomplete body')
        try:
            return raw.decode('utf-8')
        except UnicodeDecodeError:
            raise RequestError(400, 'Invalid UTF-8') from None

    def dispatch(self):
        try:
            self.route()
        except RequestError as exc:
            self.reply(exc.status, {'error': exc.message})
        except OSError:
            self.close_connection = True
        except Exception:
            self.reply(500, {'error': 'Internal server error'})

    def route(self):
        path, _, query = self.path.partition('?')
        if path.startswith('/api/'):
            credentials = self.headers.get_all('Authorization', [])
            if len(credentials) != 1 or not hmac.compare_digest(
                    credentials[0].encode('latin-1'), self.server.admin_credential):
                raise RequestError(401, 'Unauthorized')

        inbox_id = None
        argument = None
        if path == '/health':
            route, methods = 'health', ('GET',)
        elif path == '/api/inboxes':
            route, methods = 'inboxes', ('GET', 'POST')
        else:
            hook = re.fullmatch(r'/hooks/([^/]+)/([^/]+)', path)
            inbox = re.fullmatch(r'/api/inboxes/([^/]+)/(events|retention|expire)', path)
            event = re.fullmatch(r'/api/events/([^/]+)', path)
            if hook:
                route, methods = 'hook', ('POST',)
                inbox_id, argument = map(unquote, hook.groups())
            elif inbox:
                inbox_id = unquote(inbox[1])
                route = inbox[2]
                methods = {'events': ('GET',), 'retention': ('GET', 'PUT'),
                           'expire': ('POST',)}[route]
            elif event:
                route, methods = 'event', ('GET',)
                argument = unquote(event[1])
            else:
                raise RequestError(404, 'Not found')

        if self.command not in methods:
            self.reply(405, {'error': 'Method not allowed'}, ', '.join(methods))
            return
        require_length = self.command == 'PUT' or (route == 'inboxes' and self.command == 'POST')
        body = self.read_body(require_length)
        if route == 'health':
            self.reply(200, {'status': 'ok', 'schema_version': 2})
            return

        with closing(connect(self.server.db_path)) as db:
            status, payload = self.run_route(db, route, inbox_id, argument, query, body)
        self.reply(status, payload)

    def run_route(self, db, route, inbox_id, argument, query, body):
        if route == 'inboxes':
            if self.command == 'POST':
                return create_inbox(db, body)
            rows = db.execute('SELECT id, name, created_at FROM inboxes ORDER BY created_at DESC, id DESC')
            return 200, {'inboxes': [dict(row) for row in rows]}
        if route == 'hook':
            headers = {name: self.headers[name] for name in CAPTURED_HEADERS if name in self.headers}
            return receive_hook(db, inbox_id, argument, body, headers)
        if route == 'retention':
            return retention(db, inbox_id, self.command, body)
        if route == 'expire':
            return expire(db, inbox_id, body)
        if route == 'events':
            get_inbox(db, inbox_id)
            rows = db.execute(f'''SELECT {SUMMARY} FROM events WHERE inbox_id=?
                ORDER BY received_at DESC, id DESC LIMIT ?''', (inbox_id, list_limit(query)))
            return 200, {'events': [dict(row) for row in rows]}
        row = db.execute(f'SELECT {SUMMARY}, body, headers_json FROM events WHERE id=?',
                         (argument,)).fetchone()
        if row is None:
            raise RequestError(404, 'Event not found')
        result = dict(row)
        result['headers'] = json.loads(result.pop('headers_json'))
        return 200, result


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
            server.admin_credential = ('Bearer ' + token).encode('utf-8')
            server.serve_forever()
    except KeyboardInterrupt:
        pass
    except (OSError, sqlite3.Error, ValueError, RuntimeError, OverflowError):
        raise SystemExit('Phoenix could not start') from None


if __name__ == '__main__':
    main()
