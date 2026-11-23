import os
import json
import sqlite3
import hashlib
import hmac
import secrets
from datetime import datetime, timezone, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit, parse_qs, unquote

DB_PATH = os.environ.get('PHOENIX_DB_PATH', 'phoenix.sqlite')
ADMIN_TOKEN = os.environ.get('PHOENIX_ADMIN_TOKEN', '')
MAX_BODY = 65536


def connect():
    db = sqlite3.connect(DB_PATH, timeout=30, isolation_level=None)
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
            raise RuntimeError('Unsupported database schema version')
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
                max_age_seconds INTEGER NOT NULL CHECK(max_age_seconds BETWEEN 1 AND 2592000)
            )''')
            db.execute('PRAGMA user_version=2')
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def now_string():
    return datetime.now(timezone.utc).isoformat()


def digest(value):
    return hashlib.sha256(value).hexdigest()


def parse_instant(value):
    if value.endswith('Z'):
        value = value[:-1] + '+00:00'
    result = datetime.fromisoformat(value)
    if result.tzinfo is None:
        result = result.replace(tzinfo=timezone.utc)
    return result.astimezone(timezone.utc)


class HTTPError(Exception):
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
        raw = json.dumps(value, ensure_ascii=True, separators=(',', ':')).encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(raw)))
        if self.close_connection:
            self.send_header('Connection', 'close')
        self.end_headers()
        if self.command != 'HEAD':
            self.wfile.write(raw)

    def send_error(self, code, message=None, explain=None):
        self.close_connection = True
        self.send_json(code, {'error': 'Invalid HTTP request'})

    def handle_expect_100(self):
        try:
            self.check_auth(urlsplit(self.path).path)
            self.body_length()
        except HTTPError as exc:
            self.close_connection = True
            self.send_json(exc.status, {'error': exc.message})
            return False
        self.send_response_only(100)
        self.end_headers()
        return True

    def check_auth(self, path):
        if path.startswith('/api/'):
            values = self.headers.get_all('Authorization', [])
            expected = ('Bearer ' + ADMIN_TOKEN).encode('utf-8')
            if len(values) != 1 or not hmac.compare_digest(values[0].encode('utf-8'), expected):
                raise HTTPError(401, 'Unauthorized')

    def body_length(self):
        if self.headers.get_all('Transfer-Encoding') is not None:
            raise HTTPError(400, 'Transfer-Encoding is unsupported')
        values = self.headers.get_all('Content-Length', [])
        if not values:
            if self.command in ('GET', 'HEAD', 'POST', 'DELETE', 'OPTIONS', 'TRACE', 'CONNECT'):
                return 0
            raise HTTPError(400, 'Missing Content-Length')
        if len(values) != 1 or not values[0] or any(c not in '0123456789' for c in values[0]):
            raise HTTPError(400, 'Invalid Content-Length')
        try:
            length = int(values[0])
        except ValueError:
            raise HTTPError(400, 'Invalid Content-Length')
        if length > MAX_BODY:
            raise HTTPError(413, 'Body too large')
        return length

    def read_body(self):
        length = self.body_length()
        raw = self.rfile.read(length)
        if len(raw) != length:
            raise HTTPError(400, 'Incomplete body')
        try:
            return raw, raw.decode('utf-8')
        except UnicodeDecodeError:
            raise HTTPError(400, 'Invalid UTF-8')

    def json_object(self, body):
        def invalid_constant(value):
            raise ValueError('Invalid JSON constant')
        def unique_object(pairs):
            result = {}
            for key, value in pairs:
                if key in result:
                    raise ValueError('Duplicate JSON key')
                result[key] = value
            return result
        try:
            value = json.loads(body, parse_constant=invalid_constant, object_pairs_hook=unique_object)
        except (ValueError, RecursionError):
            raise HTTPError(400, 'Invalid JSON')
        if not isinstance(value, dict):
            raise HTTPError(400, 'Expected JSON object')
        return value

    def require_method(self, expected):
        if self.command not in expected:
            raise HTTPError(405, 'Method not allowed')

    def require_inbox(self, db, inbox_id):
        row = db.execute('SELECT id FROM inboxes WHERE id=?', (inbox_id,)).fetchone()
        if row is None:
            raise HTTPError(404, 'Not found')

    def dispatch(self):
        db = None
        try:
            parsed = urlsplit(self.path)
            self.check_auth(parsed.path)
            raw, body = self.read_body()
            parts = [unquote(part) for part in parsed.path.split('/')]
            db = connect()
            status, value = self.route(db, parsed, parts, raw, body)
            self.send_json(status, value)
        except HTTPError as exc:
            self.close_connection = True
            self.send_json(exc.status, {'error': exc.message})
        except Exception:
            self.close_connection = True
            self.send_json(500, {'error': 'Internal server error'})
        finally:
            if db is not None:
                if db.in_transaction:
                    db.rollback()
                db.close()

    def route(self, db, parsed, parts, raw, body):
        if parsed.path == '/health':
            self.require_method(('GET',))
            return 200, {'status': 'ok', 'schema_version': 2}
        if parsed.path == '/api/inboxes':
            self.require_method(('GET', 'POST'))
            if self.command == 'GET':
                rows = db.execute('SELECT id,name,created_at FROM inboxes ORDER BY created_at DESC,id DESC').fetchall()
                return 200, {'inboxes': [dict(row) for row in rows]}
            value = self.json_object(body)
            name = value.get('name')
            if not isinstance(name, str) or not 1 <= len(name.strip()) <= 80:
                raise HTTPError(400, 'Invalid name')
            name = name.strip()
            token = secrets.token_urlsafe(32)
            result = {'id': secrets.token_urlsafe(24), 'name': name, 'created_at': now_string(), 'token': token}
            db.execute('BEGIN IMMEDIATE')
            db.execute('INSERT INTO inboxes (id,name,token_sha256,created_at) VALUES (?,?,?,?)',
                       (result['id'], name, digest(token.encode('utf-8')), result['created_at']))
            db.commit()
            return 201, result
        if len(parts) == 4 and parts[1] == 'hooks' and parts[2] and parts[3]:
            self.require_method(('POST',))
            inbox_id, token = parts[2], parts[3]
            db.execute('BEGIN IMMEDIATE')
            inbox = db.execute('SELECT token_sha256 FROM inboxes WHERE id=?', (inbox_id,)).fetchone()
            if inbox is None or not hmac.compare_digest(inbox['token_sha256'], digest(token.encode('utf-8'))):
                raise HTTPError(404, 'Not found')
            external_id = self.headers.get('X-Webhook-Id') or None
            body_hash = digest(raw)
            if external_id is not None:
                old = db.execute('SELECT id,body FROM events WHERE inbox_id=? AND external_id=?', (inbox_id, external_id)).fetchone()
                if old is not None:
                    if old['body'] != body:
                        raise HTTPError(409, 'Idempotency key conflicts with existing body')
                    db.commit()
                    return 200, {'id': old['id'], 'duplicate': True}
            headers = {}
            for key in ('content-type', 'x-webhook-id', 'x-event-type'):
                value = self.headers.get(key)
                if value is not None:
                    headers[key] = value
            event_id = secrets.token_urlsafe(24)
            db.execute('''INSERT INTO events
                (id,inbox_id,external_id,received_at,headers_json,body,body_sha256)
                VALUES (?,?,?,?,?,?,?)''',
                       (event_id, inbox_id, external_id, now_string(), json.dumps(headers), body, body_hash))
            db.commit()
            return 201, {'id': event_id, 'duplicate': False}
        if len(parts) == 5 and parts[1:3] == ['api', 'inboxes'] and parts[3]:
            inbox_id, action = parts[3], parts[4]
            if action == 'events':
                self.require_method(('GET',))
                query = parse_qs(parsed.query, keep_blank_values=True)
                limits = query.get('limit', ['20'])
                if len(limits) != 1 or not limits[0] or any(c not in '0123456789' for c in limits[0]):
                    raise HTTPError(400, 'Invalid limit')
                try:
                    limit = int(limits[0])
                except ValueError:
                    raise HTTPError(400, 'Invalid limit')
                if not 1 <= limit <= 100:
                    raise HTTPError(400, 'Invalid limit')
                self.require_inbox(db, inbox_id)
                rows = db.execute('''SELECT id,inbox_id,external_id,received_at,body_sha256
                    FROM events WHERE inbox_id=? ORDER BY received_at DESC,id DESC LIMIT ?''', (inbox_id, limit)).fetchall()
                return 200, {'events': [dict(row) for row in rows]}
            if action == 'retention':
                self.require_method(('GET', 'PUT'))
                if self.command == 'GET':
                    self.require_inbox(db, inbox_id)
                    row = db.execute('SELECT max_age_seconds FROM retention_policies WHERE inbox_id=?', (inbox_id,)).fetchone()
                    return 200, {'max_age_seconds': row[0] if row is not None else None}
                value = self.json_object(body)
                if set(value) != {'max_age_seconds'}:
                    raise HTTPError(400, 'Invalid retention policy')
                age = value['max_age_seconds']
                if age is not None and (type(age) is not int or not 1 <= age <= 2592000):
                    raise HTTPError(400, 'Invalid max_age_seconds')
                db.execute('BEGIN IMMEDIATE')
                self.require_inbox(db, inbox_id)
                if age is None:
                    db.execute('DELETE FROM retention_policies WHERE inbox_id=?', (inbox_id,))
                else:
                    db.execute('''INSERT INTO retention_policies (inbox_id,max_age_seconds) VALUES (?,?)
                        ON CONFLICT(inbox_id) DO UPDATE SET max_age_seconds=excluded.max_age_seconds''', (inbox_id, age))
                db.commit()
                return 200, {'max_age_seconds': age}
            if action == 'expire':
                self.require_method(('POST',))
                value = {} if body == '' else self.json_object(body)
                if set(value) - {'limit'}:
                    raise HTTPError(400, 'Invalid expiration options')
                limit = value.get('limit', 100)
                if type(limit) is not int or not 1 <= limit <= 100:
                    raise HTTPError(400, 'Invalid limit')
                db.execute('BEGIN IMMEDIATE')
                self.require_inbox(db, inbox_id)
                current = datetime.now(timezone.utc)
                policy = db.execute('SELECT max_age_seconds FROM retention_policies WHERE inbox_id=?', (inbox_id,)).fetchone()
                deleted = 0
                if policy is not None:
                    cutoff = current - timedelta(seconds=policy[0])
                    candidates = []
                    for row in db.execute('SELECT id,received_at FROM events WHERE inbox_id=?', (inbox_id,)):
                        instant = parse_instant(row['received_at'])
                        if instant <= cutoff:
                            candidates.append((instant, row['id']))
                    candidates.sort()
                    for instant, event_id in candidates[:limit]:
                        cursor = db.execute('DELETE FROM events WHERE id=? AND inbox_id=?', (event_id, inbox_id))
                        deleted += cursor.rowcount
                db.commit()
                return 200, {'deleted': deleted}
        if len(parts) == 4 and parts[1:3] == ['api', 'events'] and parts[3]:
            self.require_method(('GET',))
            row = db.execute('''SELECT id,inbox_id,external_id,received_at,body_sha256,body,headers_json
                FROM events WHERE id=?''', (parts[3],)).fetchone()
            if row is None:
                raise HTTPError(404, 'Not found')
            result = dict(row)
            result['headers'] = json.loads(result.pop('headers_json'))
            return 200, result
        if self.command not in ('GET', 'POST', 'PUT'):
            raise HTTPError(405, 'Method not allowed')
        raise HTTPError(404, 'Not found')


def main():
    if not ADMIN_TOKEN:
        raise SystemExit('PHOENIX_ADMIN_TOKEN is required')
    try:
        initialize()
        port = int(os.environ.get('PORT', '8787'))
        server = ThreadingHTTPServer(('127.0.0.1', port), Handler)
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
