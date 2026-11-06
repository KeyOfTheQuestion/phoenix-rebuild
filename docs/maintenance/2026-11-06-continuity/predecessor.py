import os
import json
import sqlite3
import secrets
import hashlib
import hmac
from datetime import datetime, timezone, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit, unquote, parse_qs

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


def instant(value):
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

    def log_message(self, *args):
        pass

    def __getattr__(self, name):
        if name.startswith('do_'):
            return self.handle_request
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

    def read_body(self):
        if self.headers.get_all('Transfer-Encoding') is not None:
            raise HTTPError(400, 'Transfer-Encoding is not supported')
        lengths = self.headers.get_all('Content-Length', [])
        if not lengths:
            if self.command == 'PUT':
                raise HTTPError(400, 'Content-Length is required')
            return ''
        if len(lengths) != 1:
            raise HTTPError(400, 'Invalid Content-Length')
        value = lengths[0].strip()
        if not value or any(c not in '0123456789' for c in value):
            raise HTTPError(400, 'Invalid Content-Length')
        try:
            length = int(value)
        except ValueError:
            raise HTTPError(400, 'Invalid Content-Length')
        if length > MAX_BODY:
            raise HTTPError(413, 'Body is too large')
        raw = self.rfile.read(length)
        if len(raw) != length:
            raise HTTPError(400, 'Incomplete body')
        try:
            return raw.decode('utf-8')
        except UnicodeDecodeError:
            raise HTTPError(400, 'Invalid UTF-8')

    def json_object(self, body):
        def invalid_constant(value):
            raise ValueError('Invalid JSON constant')
        def object_pairs(pairs):
            result = {}
            for key, value in pairs:
                if key in result:
                    raise ValueError('Duplicate JSON key')
                result[key] = value
            return result
        try:
            value = json.loads(body, parse_constant=invalid_constant, object_pairs_hook=object_pairs)
        except (ValueError, RecursionError):
            raise HTTPError(400, 'Invalid JSON')
        if not isinstance(value, dict):
            raise HTTPError(400, 'Expected a JSON object')
        return value

    def require_method(self, *methods):
        if self.command not in methods:
            raise HTTPError(405, 'Method not allowed')

    def require_inbox(self, db, inbox_id):
        row = db.execute('SELECT id FROM inboxes WHERE id=?', (inbox_id,)).fetchone()
        if row is None:
            raise HTTPError(404, 'Not found')

    def handle_request(self):
        db = None
        try:
            body = self.read_body()
            parsed = urlsplit(self.path)
            path = parsed.path
            parts = [unquote(p) for p in path.split('/')]
            if path.startswith('/api/'):
                auth = self.headers.get_all('Authorization', [])
                expected = ('Bearer ' + ADMIN_TOKEN).encode('utf-8')
                if len(auth) != 1 or not hmac.compare_digest(auth[0].encode('utf-8'), expected):
                    raise HTTPError(401, 'Unauthorized')
            if path == '/health':
                self.require_method('GET')
                self.send_json(200, {'status': 'ok', 'schema_version': 2})
                return
            db = connect()
            status, value = self.route(db, path, parts, parsed.query, body)
            self.send_json(status, value)
        except HTTPError as exc:
            self.close_connection = True
            self.send_json(exc.status, {'error': exc.message})
        except (BrokenPipeError, ConnectionResetError):
            self.close_connection = True
        except Exception:
            self.close_connection = True
            self.send_json(500, {'error': 'Internal server error'})
        finally:
            if db is not None:
                if db.in_transaction:
                    db.rollback()
                db.close()

    def route(self, db, path, parts, query, body):
        if path == '/api/inboxes':
            self.require_method('GET', 'POST')
            if self.command == 'GET':
                rows = db.execute('SELECT id,name,created_at FROM inboxes ORDER BY created_at DESC,id DESC').fetchall()
                return 200, {'inboxes': [dict(row) for row in rows]}
            obj = self.json_object(body)
            name = obj.get('name')
            if not isinstance(name, str) or not 1 <= len(name.strip()) <= 80:
                raise HTTPError(400, 'Invalid name')
            token = secrets.token_urlsafe(32)
            result = {'id': secrets.token_urlsafe(24), 'name': name.strip(), 'created_at': now_text(), 'token': token}
            db.execute('BEGIN IMMEDIATE')
            db.execute('INSERT INTO inboxes (id,name,token_sha256,created_at) VALUES (?,?,?,?)',
                       (result['id'], result['name'], hashlib.sha256(token.encode()).hexdigest(), result['created_at']))
            db.commit()
            return 201, result

        if len(parts) == 4 and parts[1] == 'hooks' and parts[2] and parts[3]:
            self.require_method('POST')
            inbox_id, token = parts[2:4]
            db.execute('BEGIN IMMEDIATE')
            inbox = db.execute('SELECT token_sha256 FROM inboxes WHERE id=?', (inbox_id,)).fetchone()
            digest = hashlib.sha256(token.encode('utf-8')).hexdigest()
            if inbox is None or not hmac.compare_digest(inbox['token_sha256'], digest):
                raise HTTPError(404, 'Not found')
            external_id = self.headers.get('X-Webhook-Id') or None
            body_hash = hashlib.sha256(body.encode('utf-8')).hexdigest()
            if external_id is not None:
                old = db.execute('SELECT id,body FROM events WHERE inbox_id=? AND external_id=?', (inbox_id, external_id)).fetchone()
                if old is not None:
                    if old['body'] != body:
                        raise HTTPError(409, 'Idempotency key conflicts with an existing body')
                    db.commit()
                    return 200, {'id': old['id'], 'duplicate': True}
            headers = {}
            for key in ('content-type', 'x-webhook-id', 'x-event-type'):
                if key in self.headers:
                    headers[key] = self.headers.get(key)
            event_id = secrets.token_urlsafe(24)
            db.execute('''INSERT INTO events
                (id,inbox_id,external_id,received_at,headers_json,body,body_sha256)
                VALUES (?,?,?,?,?,?,?)''',
                (event_id, inbox_id, external_id, now_text(), json.dumps(headers), body, body_hash))
            db.commit()
            return 201, {'id': event_id, 'duplicate': False}

        if len(parts) == 5 and parts[1:3] == ['api', 'inboxes'] and parts[3]:
            inbox_id, action = parts[3:5]
            if action == 'events':
                self.require_method('GET')
                params = parse_qs(query, keep_blank_values=True)
                limits = params.get('limit', ['20'])
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
                self.require_method('GET', 'PUT')
                if self.command == 'GET':
                    self.require_inbox(db, inbox_id)
                    row = db.execute('SELECT max_age_seconds FROM retention_policies WHERE inbox_id=?', (inbox_id,)).fetchone()
                    return 200, {'max_age_seconds': row[0] if row else None}
                obj = self.json_object(body)
                if set(obj) != {'max_age_seconds'}:
                    raise HTTPError(400, 'Invalid retention policy')
                age = obj['max_age_seconds']
                if age is not None and (type(age) is not int or not 1 <= age <= 2592000):
                    raise HTTPError(400, 'Invalid retention policy')
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
                self.require_method('POST')
                obj = {} if body == '' else self.json_object(body)
                if set(obj) - {'limit'}:
                    raise HTTPError(400, 'Invalid expiry request')
                limit = obj.get('limit', 100)
                if type(limit) is not int or not 1 <= limit <= 100:
                    raise HTTPError(400, 'Invalid limit')
                db.execute('BEGIN IMMEDIATE')
                self.require_inbox(db, inbox_id)
                policy = db.execute('SELECT max_age_seconds FROM retention_policies WHERE inbox_id=?', (inbox_id,)).fetchone()
                current = datetime.now(timezone.utc)
                deleted = 0
                if policy is not None:
                    cutoff = current - timedelta(seconds=policy[0])
                    eligible = []
                    for row in db.execute('SELECT id,received_at FROM events WHERE inbox_id=?', (inbox_id,)):
                        received = instant(row['received_at'])
                        if received <= cutoff:
                            eligible.append((received, row['id']))
                    eligible.sort()
                    for _, event_id in eligible[:limit]:
                        deleted += db.execute('DELETE FROM events WHERE id=? AND inbox_id=?', (event_id, inbox_id)).rowcount
                db.commit()
                return 200, {'deleted': deleted}

        if len(parts) == 4 and parts[1:3] == ['api', 'events'] and parts[3]:
            self.require_method('GET')
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
    initialize()
    server = ThreadingHTTPServer(('127.0.0.1', int(os.environ.get('PORT', '8787'))), Handler)
    server.daemon_threads = True
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == '__main__':
    main()
