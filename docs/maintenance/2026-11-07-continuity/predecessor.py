import os
import json
import hashlib
import secrets
import hmac
import sqlite3
from datetime import datetime, timezone, timedelta
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlsplit, parse_qs, unquote

DB_PATH = os.environ.get('PHOENIX_DB_PATH', 'phoenix.sqlite')
ADMIN_TOKEN = os.environ.get('PHOENIX_ADMIN_TOKEN')
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
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def now_text():
    return datetime.now(timezone.utc).isoformat()


def digest(value):
    return hashlib.sha256(value.encode('utf-8')).hexdigest()


def instant(value):
    if value.endswith('Z'):
        value = value[:-1] + '+00:00'
    dt = datetime.fromisoformat(value)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


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
            return self.dispatch
        raise AttributeError(name)

    def send_json(self, status, obj):
        payload = json.dumps(obj, ensure_ascii=True, separators=(',', ':')).encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(payload)))
        if self.close_connection:
            self.send_header('Connection', 'close')
        self.end_headers()
        if self.command != 'HEAD':
            self.wfile.write(payload)

    def send_error(self, code, message=None, explain=None):
        self.close_connection = True
        self.send_json(code, {'error': 'HTTP request rejected'})

    def handle_expect_100(self):
        try:
            self.body_length()
        except HTTPError as exc:
            self.close_connection = True
            self.send_json(exc.status, {'error': exc.message})
            return False
        return super().handle_expect_100()

    def body_length(self):
        if self.headers.get_all('Transfer-Encoding'):
            raise HTTPError(400, 'Transfer-Encoding is unsupported')
        lengths = self.headers.get_all('Content-Length', [])
        if not lengths:
            return 0
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
            raise HTTPError(413, 'Body too large')
        return length

    def json_object(self, text):
        try:
            def invalid_constant(value):
                raise ValueError('Invalid JSON constant')
            obj = json.loads(text, parse_constant=invalid_constant)
        except (ValueError, TypeError, RecursionError):
            raise HTTPError(400, 'Invalid JSON')
        if not isinstance(obj, dict):
            raise HTTPError(400, 'Expected a JSON object')
        return obj

    def require_method(self, method):
        if self.command != method:
            raise HTTPError(405, 'Method not allowed')

    def require_inbox(self, db, inbox_id):
        row = db.execute('SELECT id, name, token_sha256, created_at FROM inboxes WHERE id=?', (inbox_id,)).fetchone()
        if row is None:
            raise HTTPError(404, 'Inbox not found')
        return row

    def dispatch(self):
        db = None
        try:
            try:
                length = self.body_length()
                raw = self.rfile.read(length)
                if len(raw) != length:
                    raise HTTPError(400, 'Incomplete body')
                text = raw.decode('utf-8')
            except UnicodeDecodeError:
                self.close_connection = True
                raise HTTPError(400, 'Invalid UTF-8')
            except HTTPError:
                self.close_connection = True
                raise
            try:
                url = urlsplit(self.path)
                path = url.path
            except ValueError:
                raise HTTPError(400, 'Invalid URL')
            if path.startswith('/api/'):
                values = self.headers.get_all('Authorization', [])
                expected = ('Bearer ' + ADMIN_TOKEN).encode('utf-8')
                if len(values) != 1 or not hmac.compare_digest(values[0].encode('utf-8'), expected):
                    raise HTTPError(401, 'Unauthorized')
            if path == '/health':
                self.require_method('GET')
                self.send_json(200, {'status': 'ok', 'schema_version': 2})
                return
            parts = [unquote(p) for p in path.split('/')]
            db = connect()
            status, result = self.route(db, path, parts, url.query, text)
            self.send_json(status, result)
        except HTTPError as exc:
            self.close_connection = True
            self.send_json(exc.status, {'error': exc.message})
        except Exception:
            self.close_connection = True
            try:
                self.send_json(500, {'error': 'Internal server error'})
            except (OSError, ValueError):
                pass
        finally:
            if db is not None:
                db.close()

    def route(self, db, path, p, query, text):
        if path == '/api/inboxes':
            if self.command == 'GET':
                rows = db.execute('SELECT id, name, created_at FROM inboxes ORDER BY created_at DESC, id DESC').fetchall()
                return 200, {'inboxes': [dict(row) for row in rows]}
            self.require_method('POST')
            obj = self.json_object(text)
            name = obj.get('name')
            if not isinstance(name, str) or not 1 <= len(name.strip()) <= 80:
                raise HTTPError(400, 'Invalid name')
            name = name.strip()
            token = secrets.token_urlsafe(32)
            inbox_id = secrets.token_hex(16)
            created = now_text()
            db.execute('INSERT INTO inboxes (id, name, token_sha256, created_at) VALUES (?, ?, ?, ?)', (inbox_id, name, digest(token), created))
            db.commit()
            return 201, {'id': inbox_id, 'name': name, 'created_at': created, 'token': token}

        if len(p) == 4 and p[1] == 'hooks' and p[2] and p[3]:
            self.require_method('POST')
            inbox_id, token = p[2], p[3]
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT token_sha256 FROM inboxes WHERE id=?', (inbox_id,)).fetchone()
            if row is None or not hmac.compare_digest(row['token_sha256'], digest(token)):
                raise HTTPError(404, 'Hook not found')
            external = self.headers.get('X-Webhook-Id') or None
            body_hash = digest(text)
            if external is not None:
                existing = db.execute('SELECT id, body FROM events WHERE inbox_id=? AND external_id=?', (inbox_id, external)).fetchone()
                if existing is not None:
                    if existing['body'] != text:
                        raise HTTPError(409, 'Idempotency key conflicts with existing body')
                    db.commit()
                    return 200, {'id': existing['id'], 'duplicate': True}
            headers = {}
            for name in ('content-type', 'x-webhook-id', 'x-event-type'):
                value = self.headers.get(name)
                if value is not None:
                    headers[name] = value
            event_id = secrets.token_hex(16)
            db.execute('INSERT INTO events (id, inbox_id, external_id, received_at, headers_json, body, body_sha256) VALUES (?, ?, ?, ?, ?, ?, ?)', (event_id, inbox_id, external, now_text(), json.dumps(headers), text, body_hash))
            db.commit()
            return 201, {'id': event_id, 'duplicate': False}

        if len(p) == 5 and p[1:3] == ['api', 'inboxes'] and p[3]:
            inbox_id, action = p[3], p[4]
            if action == 'events':
                self.require_method('GET')
                params = parse_qs(query, keep_blank_values=True)
                values = params.get('limit', ['20'])
                if len(values) != 1 or not values[0] or any(c not in '0123456789' for c in values[0]):
                    raise HTTPError(400, 'Invalid limit')
                try:
                    limit = int(values[0])
                except ValueError:
                    raise HTTPError(400, 'Invalid limit')
                if not 1 <= limit <= 100:
                    raise HTTPError(400, 'Invalid limit')
                self.require_inbox(db, inbox_id)
                rows = db.execute('SELECT id, inbox_id, external_id, received_at, body_sha256 FROM events WHERE inbox_id=? ORDER BY received_at DESC, id DESC LIMIT ?', (inbox_id, limit)).fetchall()
                return 200, {'events': [dict(row) for row in rows]}
            if action == 'retention':
                if self.command not in ('GET', 'PUT'):
                    raise HTTPError(405, 'Method not allowed')
                if self.command == 'GET':
                    self.require_inbox(db, inbox_id)
                    row = db.execute('SELECT max_age_seconds FROM retention_policies WHERE inbox_id=?', (inbox_id,)).fetchone()
                    return 200, {'max_age_seconds': None if row is None else row[0]}
                obj = self.json_object(text)
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
                    db.execute('INSERT INTO retention_policies (inbox_id, max_age_seconds) VALUES (?, ?) ON CONFLICT(inbox_id) DO UPDATE SET max_age_seconds=excluded.max_age_seconds', (inbox_id, age))
                db.commit()
                return 200, {'max_age_seconds': age}
            if action == 'expire':
                self.require_method('POST')
                obj = {} if text == '' else self.json_object(text)
                if set(obj) - {'limit'}:
                    raise HTTPError(400, 'Invalid expiry request')
                limit = obj.get('limit', 100)
                if type(limit) is not int or not 1 <= limit <= 100:
                    raise HTTPError(400, 'Invalid limit')
                db.execute('BEGIN IMMEDIATE')
                self.require_inbox(db, inbox_id)
                policy = db.execute('SELECT max_age_seconds FROM retention_policies WHERE inbox_id=?', (inbox_id,)).fetchone()
                batch_now = datetime.now(timezone.utc)
                selected = []
                if policy is not None:
                    cutoff = batch_now - timedelta(seconds=policy[0])
                    for row in db.execute('SELECT id, received_at FROM events WHERE inbox_id=?', (inbox_id,)):
                        timestamp = instant(row['received_at'])
                        if timestamp <= cutoff:
                            selected.append((timestamp, row['id']))
                    selected.sort()
                    selected = selected[:limit]
                deleted = 0
                for _, event_id in selected:
                    deleted += db.execute('DELETE FROM events WHERE id=? AND inbox_id=?', (event_id, inbox_id)).rowcount
                db.commit()
                return 200, {'deleted': deleted}

        if len(p) == 4 and p[1:3] == ['api', 'events'] and p[3]:
            self.require_method('GET')
            row = db.execute('SELECT id, inbox_id, external_id, received_at, body_sha256, body, headers_json FROM events WHERE id=?', (p[3],)).fetchone()
            if row is None:
                raise HTTPError(404, 'Event not found')
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
