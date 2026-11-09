import os
import json
import sqlite3
import hashlib
import secrets
import hmac
import re
from datetime import datetime, timezone, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit, parse_qs

DB_PATH = os.environ.get('PHOENIX_DB_PATH', 'phoenix.sqlite')
ADMIN_TOKEN = os.environ.get('PHOENIX_ADMIN_TOKEN', '')
MAX_BODY = 65536


def connect():
    db = sqlite3.connect(DB_PATH, timeout=30, isolation_level=None)
    db.row_factory = sqlite3.Row
    db.execute('PRAGMA foreign_keys=ON')
    db.execute('PRAGMA busy_timeout=30000')
    return db


def migrate():
    db = connect()
    try:
        db.execute('BEGIN IMMEDIATE')
        version = db.execute('PRAGMA user_version').fetchone()[0]
        if version not in (0, 1, 2):
            raise RuntimeError('Unsupported schema version')
        if version in (0, 1):
            db.execute('''CREATE TABLE IF NOT EXISTS inboxes (
                id TEXT PRIMARY KEY, name TEXT NOT NULL,
                token_sha256 TEXT NOT NULL, created_at TEXT NOT NULL)''')
            db.execute('''CREATE TABLE IF NOT EXISTS events (
                id TEXT PRIMARY KEY,
                inbox_id TEXT NOT NULL REFERENCES inboxes(id),
                external_id TEXT, received_at TEXT NOT NULL,
                headers_json TEXT NOT NULL, body TEXT NOT NULL,
                body_sha256 TEXT NOT NULL, UNIQUE(inbox_id, external_id))''')
            db.execute('''CREATE TABLE IF NOT EXISTS retention_policies (
                inbox_id TEXT PRIMARY KEY REFERENCES inboxes(id),
                max_age_seconds INTEGER NOT NULL
                CHECK(max_age_seconds BETWEEN 1 AND 2592000))''')
            db.execute('PRAGMA user_version=2')
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def now_text():
    return datetime.now(timezone.utc).isoformat()


def digest(value):
    return hashlib.sha256(value).hexdigest()


def instant(value):
    if value.endswith('Z'):
        value = value[:-1] + '+00:00'
    result = datetime.fromisoformat(value)
    if result.tzinfo is None:
        result = result.replace(tzinfo=timezone.utc)
    return result.astimezone(timezone.utc)


class Failure(Exception):
    def __init__(self, status, message):
        self.status = status
        self.message = message


class Handler(BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'

    def log_message(self, format, *args):
        pass

    def __getattr__(self, name):
        if name.startswith('do_'):
            return self.dispatch
        raise AttributeError(name)

    def send_json(self, status, value):
        payload = json.dumps(value, ensure_ascii=True, separators=(',', ':')).encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(payload)))
        if self.close_connection:
            self.send_header('Connection', 'close')
        self.end_headers()
        self.wfile.write(payload)

    def send_error(self, code, message=None, explain=None):
        self.close_connection = True
        self.send_json(code, {'error': 'HTTP request rejected'})

    def fail(self, status, message):
        raise Failure(status, message)

    def read_body(self):
        if self.headers.get_all('Transfer-Encoding') is not None:
            self.fail(400, 'Transfer-Encoding is unsupported')
        lengths = self.headers.get_all('Content-Length', [])
        if not lengths:
            if self.command == 'PUT':
                self.fail(400, 'Content-Length is required')
            return ''
        if len(lengths) != 1 or re.fullmatch(r'[0-9]+', lengths[0]) is None:
            self.fail(400, 'Invalid Content-Length')
        try:
            length = int(lengths[0])
        except ValueError:
            self.fail(400, 'Invalid Content-Length')
        if length > MAX_BODY:
            self.fail(413, 'Body too large')
        raw = self.rfile.read(length)
        if len(raw) != length:
            self.fail(400, 'Incomplete body')
        try:
            return raw.decode('utf-8')
        except UnicodeDecodeError:
            self.fail(400, 'Invalid UTF-8')

    def object_body(self, body):
        try:
            value = json.loads(body, parse_constant=lambda value: (_ for _ in ()).throw(ValueError()))
        except (ValueError, RecursionError):
            self.fail(400, 'Invalid JSON')
        if type(value) is not dict:
            self.fail(400, 'Expected a JSON object')
        return value

    def require_method(self, *methods):
        if self.command not in methods:
            self.fail(405, 'Method not allowed')

    def require_inbox(self, db, inbox_id):
        row = db.execute('SELECT id FROM inboxes WHERE id=?', (inbox_id,)).fetchone()
        if row is None:
            self.fail(404, 'Inbox not found')

    def dispatch(self):
        db = None
        try:
            parsed = urlsplit(self.path)
            path = parsed.path
            if path.startswith('/api/'):
                credentials = self.headers.get_all('Authorization', [])
                expected = ('Bearer ' + ADMIN_TOKEN).encode('utf-8')
                if len(credentials) != 1 or not hmac.compare_digest(credentials[0].encode('utf-8'), expected):
                    self.fail(401, 'Unauthorized')
            body = self.read_body()
            db = connect()
            status, result = self.route(db, path, parsed.query, body)
            db.commit()
            self.send_json(status, result)
        except Failure as exc:
            if db is not None:
                db.rollback()
            self.close_connection = True
            self.send_json(exc.status, {'error': exc.message})
        except Exception:
            if db is not None:
                db.rollback()
            self.close_connection = True
            self.send_json(500, {'error': 'Internal server error'})
        finally:
            if db is not None:
                db.close()

    def route(self, db, path, query, body):
        if path == '/health':
            self.require_method('GET')
            return 200, {'status': 'ok', 'schema_version': 2}
        if path == '/api/inboxes':
            self.require_method('GET', 'POST')
            if self.command == 'GET':
                rows = db.execute('SELECT id,name,created_at FROM inboxes ORDER BY created_at DESC,id DESC').fetchall()
                return 200, {'inboxes': [dict(row) for row in rows]}
            value = self.object_body(body)
            name = value.get('name')
            if not isinstance(name, str) or not 1 <= len(name.strip()) <= 80:
                self.fail(400, 'Invalid name')
            name = name.strip()
            inbox_id = secrets.token_hex(16)
            token = secrets.token_urlsafe(32)
            created_at = now_text()
            db.execute('BEGIN IMMEDIATE')
            db.execute('INSERT INTO inboxes (id,name,token_sha256,created_at) VALUES (?,?,?,?)',
                       (inbox_id, name, digest(token.encode('utf-8')), created_at))
            return 201, {'id': inbox_id, 'name': name, 'created_at': created_at, 'token': token}
        match = re.fullmatch(r'/hooks/([^/]+)/([^/]+)', path)
        if match:
            self.require_method('POST')
            inbox_id, token = match.groups()
            db.execute('BEGIN IMMEDIATE')
            inbox = db.execute('SELECT token_sha256 FROM inboxes WHERE id=?', (inbox_id,)).fetchone()
            if inbox is None or not hmac.compare_digest(inbox['token_sha256'], digest(token.encode('utf-8'))):
                self.fail(404, 'Hook not found')
            external_id = self.headers.get('X-Webhook-Id') or None
            body_hash = digest(body.encode('utf-8'))
            if external_id is not None:
                old = db.execute('SELECT id,body FROM events WHERE inbox_id=? AND external_id=?',
                                 (inbox_id, external_id)).fetchone()
                if old is not None:
                    if old['body'] != body:
                        self.fail(409, 'Idempotency key conflicts with existing body')
                    return 200, {'id': old['id'], 'duplicate': True}
            headers = {}
            for key in ('content-type', 'x-webhook-id', 'x-event-type'):
                value = self.headers.get(key)
                if value is not None:
                    headers[key] = value
            event_id = secrets.token_hex(16)
            db.execute('''INSERT INTO events
                (id,inbox_id,external_id,received_at,headers_json,body,body_sha256)
                VALUES (?,?,?,?,?,?,?)''',
                       (event_id, inbox_id, external_id, now_text(), json.dumps(headers), body, body_hash))
            return 201, {'id': event_id, 'duplicate': False}
        match = re.fullmatch(r'/api/inboxes/([^/]+)/(events|retention|expire)', path)
        if match:
            inbox_id, operation = match.groups()
            if operation == 'events':
                self.require_method('GET')
                params = parse_qs(query, keep_blank_values=True)
                limits = params.get('limit', ['20'])
                if len(limits) != 1 or re.fullmatch(r'[0-9]+', limits[0]) is None:
                    self.fail(400, 'Invalid limit')
                try:
                    limit = int(limits[0])
                except ValueError:
                    self.fail(400, 'Invalid limit')
                if not 1 <= limit <= 100:
                    self.fail(400, 'Invalid limit')
                self.require_inbox(db, inbox_id)
                rows = db.execute('''SELECT id,inbox_id,external_id,received_at,body_sha256
                    FROM events WHERE inbox_id=? ORDER BY received_at DESC,id DESC LIMIT ?''',
                                  (inbox_id, limit)).fetchall()
                return 200, {'events': [dict(row) for row in rows]}
            if operation == 'retention':
                self.require_method('GET', 'PUT')
                if self.command == 'GET':
                    db.execute('BEGIN')
                    self.require_inbox(db, inbox_id)
                    row = db.execute('SELECT max_age_seconds FROM retention_policies WHERE inbox_id=?', (inbox_id,)).fetchone()
                    return 200, {'max_age_seconds': row[0] if row is not None else None}
                value = self.object_body(body)
                if set(value) != {'max_age_seconds'}:
                    self.fail(400, 'Invalid policy')
                age = value['max_age_seconds']
                if age is not None and (type(age) is not int or not 1 <= age <= 2592000):
                    self.fail(400, 'Invalid policy')
                db.execute('BEGIN IMMEDIATE')
                self.require_inbox(db, inbox_id)
                if age is None:
                    db.execute('DELETE FROM retention_policies WHERE inbox_id=?', (inbox_id,))
                else:
                    db.execute('''INSERT INTO retention_policies (inbox_id,max_age_seconds) VALUES (?,?)
                        ON CONFLICT(inbox_id) DO UPDATE SET max_age_seconds=excluded.max_age_seconds''', (inbox_id, age))
                return 200, {'max_age_seconds': age}
            self.require_method('POST')
            value = {} if body == '' else self.object_body(body)
            if not set(value).issubset({'limit'}):
                self.fail(400, 'Invalid expiry request')
            limit = value.get('limit', 100)
            if type(limit) is not int or not 1 <= limit <= 100:
                self.fail(400, 'Invalid limit')
            db.execute('BEGIN IMMEDIATE')
            self.require_inbox(db, inbox_id)
            current_time = datetime.now(timezone.utc)
            policy = db.execute('SELECT max_age_seconds FROM retention_policies WHERE inbox_id=?', (inbox_id,)).fetchone()
            if policy is None:
                return 200, {'deleted': 0}
            cutoff = current_time - timedelta(seconds=policy[0])
            eligible = []
            for row in db.execute('SELECT id,received_at FROM events WHERE inbox_id=?', (inbox_id,)):
                received = instant(row['received_at'])
                if received <= cutoff:
                    eligible.append((received, row['id']))
            eligible.sort()
            deleted = 0
            for received, event_id in eligible[:limit]:
                deleted += db.execute('DELETE FROM events WHERE id=? AND inbox_id=?', (event_id, inbox_id)).rowcount
            return 200, {'deleted': deleted}
        match = re.fullmatch(r'/api/events/([^/]+)', path)
        if match:
            self.require_method('GET')
            row = db.execute('''SELECT id,inbox_id,external_id,received_at,body_sha256,body,headers_json
                FROM events WHERE id=?''', (match.group(1),)).fetchone()
            if row is None:
                self.fail(404, 'Event not found')
            result = dict(row)
            result['headers'] = json.loads(result.pop('headers_json'))
            return 200, result
        if self.command not in ('GET', 'POST', 'PUT'):
            self.fail(405, 'Method not allowed')
        self.fail(404, 'Not found')


def main():
    if not ADMIN_TOKEN:
        raise SystemExit('PHOENIX_ADMIN_TOKEN is required')
    try:
        migrate()
        server = ThreadingHTTPServer(('127.0.0.1', int(os.environ.get('PORT', '8787'))), Handler)
    except Exception:
        raise SystemExit('Service startup failed')
    server.daemon_threads = True
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == '__main__':
    main()
