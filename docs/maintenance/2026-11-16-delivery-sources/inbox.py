import os
import sys
import json
import re
import secrets
import hashlib
import hmac
import sqlite3
from datetime import datetime, timezone, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit, parse_qs, unquote

DB_PATH = os.environ.get('PHOENIX_DB_PATH', 'phoenix.sqlite')
ADMIN_TOKEN = os.environ.get('PHOENIX_ADMIN_TOKEN', '')
MAX_BODY = 65536


def connect():
    db = sqlite3.connect(DB_PATH, timeout=30)
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
                max_age_seconds INTEGER NOT NULL
                    CHECK(max_age_seconds BETWEEN 1 AND 2592000)
            )''')
            db.execute('PRAGMA user_version=2')
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def timestamp():
    return datetime.now(timezone.utc).isoformat()


def digest(value):
    return hashlib.sha256(value.encode('utf-8')).hexdigest()


def summary(row):
    return {key: row[key] for key in (
        'id', 'inbox_id', 'external_id', 'received_at', 'body_sha256')}


class RequestError(Exception):
    def __init__(self, status, message, close=False):
        self.status = status
        self.message = message
        self.close = close


class Handler(BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'

    def log_message(self, format, *args):
        pass

    def send_json(self, status, value):
        data = json.dumps(value, ensure_ascii=True, separators=(',', ':')).encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(data)))
        if self.close_connection:
            self.send_header('Connection', 'close')
        self.end_headers()
        if self.command != 'HEAD':
            self.wfile.write(data)

    def send_error(self, code, message=None, explain=None):
        self.close_connection = True
        self.send_json(code, {'error': 'Invalid HTTP request'})

    def handle_expect_100(self):
        return True

    def read_body(self):
        if self.headers.get_all('Transfer-Encoding') is not None:
            raise RequestError(400, 'Transfer-Encoding is not supported', True)
        lengths = self.headers.get_all('Content-Length', [])
        if not lengths:
            length = 0
        elif len(lengths) != 1 or re.fullmatch(r'[0-9]+', lengths[0]) is None:
            raise RequestError(400, 'Invalid Content-Length', True)
        else:
            value = lengths[0].lstrip('0') or '0'
            if len(value) > 5:
                raise RequestError(413, 'Body too large', True)
            length = int(value)
        if length > MAX_BODY:
            raise RequestError(413, 'Body too large', True)
        if self.headers.get('Expect', '').lower() == '100-continue':
            self.send_response_only(100)
            self.end_headers()
        data = self.rfile.read(length)
        if len(data) != length:
            raise RequestError(400, 'Incomplete request body', True)
        try:
            return data.decode('utf-8')
        except UnicodeDecodeError:
            raise RequestError(400, 'Body must be UTF-8', True)

    def parse_json(self, body):
        def invalid_constant(value):
            raise ValueError('Invalid JSON constant')
        try:
            return json.loads(body, parse_constant=invalid_constant)
        except (ValueError, RecursionError):
            raise RequestError(400, 'Invalid JSON', True)

    def require_method(self, *methods):
        if self.command not in methods:
            raise RequestError(405, 'Method not allowed')

    def require_inbox(self, db, inbox_id):
        row = db.execute('SELECT id FROM inboxes WHERE id=?', (inbox_id,)).fetchone()
        if row is None:
            raise RequestError(404, 'Not found')

    def dispatch(self):
        db = None
        try:
            parsed = urlsplit(self.path)
            path = parsed.path
            if path.startswith('/api/'):
                auth = self.headers.get_all('Authorization', [])
                expected = ('Bearer ' + ADMIN_TOKEN).encode('utf-8')
                if len(auth) != 1 or not hmac.compare_digest(auth[0].encode('utf-8'), expected):
                    raise RequestError(401, 'Unauthorized', True)
            body = self.read_body()
            parts = [unquote(part) for part in path.split('/')]
            if path == '/health':
                self.require_method('GET')
                self.send_json(200, {'status': 'ok', 'schema_version': 2})
                return
            db = connect()
            if path == '/api/inboxes':
                self.require_method('GET', 'POST')
                if self.command == 'GET':
                    rows = db.execute('SELECT id,name,created_at FROM inboxes ORDER BY created_at DESC,id DESC').fetchall()
                    self.send_json(200, {'inboxes': [dict(row) for row in rows]})
                    return
                value = self.parse_json(body)
                if not isinstance(value, dict) or not isinstance(value.get('name'), str):
                    raise RequestError(400, 'Invalid name', True)
                name = value['name'].strip()
                if not 1 <= len(name) <= 80:
                    raise RequestError(400, 'Invalid name', True)
                inbox_id = secrets.token_urlsafe(24)
                token = secrets.token_urlsafe(32)
                created = timestamp()
                db.execute('INSERT INTO inboxes (id,name,token_sha256,created_at) VALUES (?,?,?,?)',
                           (inbox_id, name, digest(token), created))
                db.commit()
                self.send_json(201, {'id': inbox_id, 'name': name, 'created_at': created, 'token': token})
                return
            if len(parts) == 4 and parts[1] == 'hooks':
                self.require_method('POST')
                inbox_id, token = parts[2:]
                db.execute('BEGIN IMMEDIATE')
                inbox = db.execute('SELECT token_sha256 FROM inboxes WHERE id=?', (inbox_id,)).fetchone()
                if inbox is None or not hmac.compare_digest(inbox['token_sha256'], digest(token)):
                    raise RequestError(404, 'Not found')
                external_id = self.headers.get('X-Webhook-Id') or None
                body_hash = digest(body)
                if external_id is not None:
                    old = db.execute('SELECT id,body FROM events WHERE inbox_id=? AND external_id=?',
                                     (inbox_id, external_id)).fetchone()
                    if old is not None:
                        if old['body'] != body:
                            raise RequestError(409, 'Idempotency key conflicts with existing body')
                        db.commit()
                        self.send_json(200, {'id': old['id'], 'duplicate': True})
                        return
                headers = {}
                for name in ('content-type', 'x-webhook-id', 'x-event-type'):
                    if name in self.headers:
                        headers[name] = self.headers[name]
                event_id = secrets.token_urlsafe(24)
                db.execute('''INSERT INTO events
                    (id,inbox_id,external_id,received_at,headers_json,body,body_sha256)
                    VALUES (?,?,?,?,?,?,?)''',
                    (event_id, inbox_id, external_id, timestamp(), json.dumps(headers), body, body_hash))
                db.commit()
                self.send_json(201, {'id': event_id, 'duplicate': False})
                return
            if len(parts) == 4 and parts[1:3] == ['api', 'events']:
                self.require_method('GET')
                row = db.execute('SELECT * FROM events WHERE id=?', (parts[3],)).fetchone()
                if row is None:
                    raise RequestError(404, 'Not found')
                result = summary(row)
                result.update(body=row['body'], headers=json.loads(row['headers_json']))
                self.send_json(200, result)
                return
            if len(parts) == 5 and parts[1:3] == ['api', 'inboxes']:
                inbox_id, action = parts[3:]
                if action == 'events':
                    self.require_method('GET')
                    query = parse_qs(parsed.query, keep_blank_values=True)
                    values = query.get('limit', ['20'])
                    if len(values) != 1 or re.fullmatch(r'[0-9]+', values[0]) is None:
                        raise RequestError(400, 'Invalid limit')
                    number = values[0].lstrip('0') or '0'
                    if len(number) > 3 or not 1 <= int(number) <= 100:
                        raise RequestError(400, 'Invalid limit')
                    self.require_inbox(db, inbox_id)
                    rows = db.execute('''SELECT id,inbox_id,external_id,received_at,body_sha256
                        FROM events WHERE inbox_id=? ORDER BY received_at DESC,id DESC LIMIT ?''',
                        (inbox_id, int(number))).fetchall()
                    self.send_json(200, {'events': [summary(row) for row in rows]})
                    return
                if action == 'retention':
                    self.require_method('GET', 'PUT')
                    if self.command == 'GET':
                        self.require_inbox(db, inbox_id)
                        row = db.execute('SELECT max_age_seconds FROM retention_policies WHERE inbox_id=?', (inbox_id,)).fetchone()
                        self.send_json(200, {'max_age_seconds': None if row is None else row[0]})
                        return
                    value = self.parse_json(body)
                    if not isinstance(value, dict) or set(value) != {'max_age_seconds'}:
                        raise RequestError(400, 'Invalid retention policy', True)
                    age = value['max_age_seconds']
                    if age is not None and (type(age) is not int or not 1 <= age <= 2592000):
                        raise RequestError(400, 'Invalid retention policy', True)
                    db.execute('BEGIN IMMEDIATE')
                    self.require_inbox(db, inbox_id)
                    if age is None:
                        db.execute('DELETE FROM retention_policies WHERE inbox_id=?', (inbox_id,))
                    else:
                        db.execute('''INSERT INTO retention_policies (inbox_id,max_age_seconds) VALUES (?,?)
                            ON CONFLICT(inbox_id) DO UPDATE SET max_age_seconds=excluded.max_age_seconds''', (inbox_id, age))
                    db.commit()
                    self.send_json(200, {'max_age_seconds': age})
                    return
                if action == 'expire':
                    self.require_method('POST')
                    value = {} if body == '' else self.parse_json(body)
                    if not isinstance(value, dict) or set(value) - {'limit'}:
                        raise RequestError(400, 'Invalid expiration request', True)
                    limit = value.get('limit', 100)
                    if type(limit) is not int or not 1 <= limit <= 100:
                        raise RequestError(400, 'Invalid limit', True)
                    db.execute('BEGIN IMMEDIATE')
                    self.require_inbox(db, inbox_id)
                    policy = db.execute('SELECT max_age_seconds FROM retention_policies WHERE inbox_id=?', (inbox_id,)).fetchone()
                    now = datetime.now(timezone.utc)
                    eligible = []
                    if policy is not None:
                        cutoff = now - timedelta(seconds=policy[0])
                        for row in db.execute('SELECT id,received_at FROM events WHERE inbox_id=?', (inbox_id,)):
                            instant = datetime.fromisoformat(row['received_at'].replace('Z', '+00:00'))
                            if instant.tzinfo is None:
                                instant = instant.replace(tzinfo=timezone.utc)
                            if instant <= cutoff:
                                eligible.append((instant, row['id']))
                    eligible.sort()
                    ids = [item[1] for item in eligible[:limit]]
                    deleted = 0
                    for event_id in ids:
                        deleted += db.execute('DELETE FROM events WHERE inbox_id=? AND id=?', (inbox_id, event_id)).rowcount
                    db.commit()
                    self.send_json(200, {'deleted': deleted})
                    return
            raise RequestError(404, 'Not found')
        except RequestError as exc:
            if db is not None:
                db.rollback()
            if exc.close:
                self.close_connection = True
            self.send_json(exc.status, {'error': exc.message})
        except (BrokenPipeError, ConnectionResetError):
            self.close_connection = True
        except Exception:
            if db is not None:
                db.rollback()
            self.close_connection = True
            self.send_json(500, {'error': 'Internal server error'})
        finally:
            if db is not None:
                db.close()

    do_GET = dispatch
    do_POST = dispatch
    do_PUT = dispatch
    do_DELETE = dispatch
    do_PATCH = dispatch
    do_HEAD = dispatch
    do_OPTIONS = dispatch
    do_TRACE = dispatch
    do_CONNECT = dispatch

    def __getattr__(self, name):
        if name.startswith('do_'):
            return self.dispatch
        raise AttributeError(name)


def main():
    if not ADMIN_TOKEN:
        sys.stderr.write('PHOENIX_ADMIN_TOKEN is required\n')
        return 1
    try:
        port = int(os.environ.get('PORT', '8787'))
        initialize()
        server = ThreadingHTTPServer(('127.0.0.1', port), Handler)
        server.daemon_threads = True
    except Exception:
        sys.stderr.write('Service startup failed\n')
        return 1
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == '__main__':
    sys.exit(main())
