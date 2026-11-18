import os
import json
import sqlite3
import secrets
import hashlib
import hmac
import re
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


def parse_instant(value):
    if value.endswith('Z'):
        value = value[:-1] + '+00:00'
    result = datetime.fromisoformat(value)
    if result.tzinfo is None:
        result = result.replace(tzinfo=timezone.utc)
    return result.astimezone(timezone.utc)


class RequestError(Exception):
    def __init__(self, status, message):
        self.status = status
        self.message = message


class Handler(BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'

    def log_message(self, *args):
        pass

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
        if code == 501:
            code = 405
        self.send_json(code, {'error': 'Method not allowed' if code == 405 else 'Invalid request'})

    def handle_expect_100(self):
        return True

    def read_body(self):
        if self.headers.get_all('Transfer-Encoding'):
            raise RequestError(400, 'Transfer-Encoding is not supported')
        lengths = self.headers.get_all('Content-Length', [])
        if not lengths:
            if self.command in ('POST', 'GET', 'HEAD', 'DELETE', 'OPTIONS'):
                length = 0
            else:
                raise RequestError(400, 'Missing Content-Length')
        elif len(lengths) != 1 or re.fullmatch(r'[0-9]+', lengths[0].strip()) is None:
            raise RequestError(400, 'Invalid Content-Length')
        else:
            try:
                length = int(lengths[0].strip())
            except ValueError:
                raise RequestError(400, 'Invalid Content-Length')
        if length > MAX_BODY:
            raise RequestError(413, 'Body too large')
        if self.headers.get('Expect', '').lower() == '100-continue':
            self.send_response_only(100)
            self.end_headers()
        raw = self.rfile.read(length)
        if len(raw) != length:
            raise RequestError(400, 'Incomplete body')
        try:
            return raw, raw.decode('utf-8')
        except UnicodeDecodeError:
            raise RequestError(400, 'Invalid UTF-8')

    def json_object(self, text):
        def pairs(items):
            result = {}
            for key, value in items:
                if key in result:
                    raise ValueError('Duplicate key')
                result[key] = value
            return result
        try:
            value = json.loads(text, object_pairs_hook=pairs,
                               parse_constant=lambda x: (_ for _ in ()).throw(ValueError()))
        except (ValueError, TypeError):
            raise RequestError(400, 'Invalid JSON')
        if not isinstance(value, dict):
            raise RequestError(400, 'Expected a JSON object')
        return value

    def require_method(self, *methods):
        if self.command not in methods:
            raise RequestError(405, 'Method not allowed')

    def require_inbox(self, db, inbox_id):
        row = db.execute('SELECT id FROM inboxes WHERE id=?', (inbox_id,)).fetchone()
        if row is None:
            raise RequestError(404, 'Not found')

    def dispatch(self, db, path, query, raw, text):
        parts = path.split('/')
        if path == '/health':
            self.require_method('GET')
            return 200, {'status': 'ok', 'schema_version': 2}
        if path == '/api/inboxes':
            self.require_method('GET', 'POST')
            if self.command == 'GET':
                rows = db.execute('SELECT id,name,created_at FROM inboxes ORDER BY created_at DESC,id DESC').fetchall()
                return 200, {'inboxes': [dict(row) for row in rows]}
            value = self.json_object(text)
            name = value.get('name')
            if not isinstance(name, str) or not 1 <= len(name.strip()) <= 80:
                raise RequestError(400, 'Invalid name')
            name = name.strip()
            token = secrets.token_urlsafe(32)
            inbox_id = secrets.token_hex(16)
            created = now_text()
            db.execute('INSERT INTO inboxes (id,name,token_sha256,created_at) VALUES (?,?,?,?)',
                       (inbox_id, name, digest(token.encode('utf-8')), created))
            db.commit()
            return 201, {'id': inbox_id, 'name': name, 'created_at': created, 'token': token}
        if len(parts) == 4 and parts[1] == 'hooks' and parts[2] and parts[3]:
            self.require_method('POST')
            inbox_id, token = parts[2:]
            db.execute('BEGIN IMMEDIATE')
            inbox = db.execute('SELECT token_sha256 FROM inboxes WHERE id=?', (inbox_id,)).fetchone()
            if inbox is None or not hmac.compare_digest(inbox['token_sha256'], digest(token.encode('utf-8'))):
                raise RequestError(404, 'Not found')
            external_id = self.headers.get('X-Webhook-Id') or None
            body_hash = digest(raw)
            if external_id is not None:
                previous = db.execute('SELECT id,body_sha256,body FROM events WHERE inbox_id=? AND external_id=?',
                                      (inbox_id, external_id)).fetchone()
                if previous is not None:
                    if previous['body_sha256'] != body_hash or previous['body'] != text:
                        raise RequestError(409, 'Idempotency key conflicts with existing body')
                    db.commit()
                    return 200, {'id': previous['id'], 'duplicate': True}
            captured = {}
            for key in ('content-type', 'x-webhook-id', 'x-event-type'):
                if self.headers.get(key) is not None:
                    captured[key] = self.headers.get(key)
            event_id = secrets.token_hex(16)
            db.execute('''INSERT INTO events
                (id,inbox_id,external_id,received_at,headers_json,body,body_sha256)
                VALUES (?,?,?,?,?,?,?)''',
                       (event_id, inbox_id, external_id, now_text(), json.dumps(captured), text, body_hash))
            db.commit()
            return 201, {'id': event_id, 'duplicate': False}
        if len(parts) == 5 and parts[1:3] == ['api', 'inboxes'] and parts[3]:
            inbox_id, action = parts[3:]
            if action == 'events':
                self.require_method('GET')
                params = parse_qs(query, keep_blank_values=True)
                values = params.get('limit', ['20'])
                if len(values) != 1 or re.fullmatch(r'[0-9]+', values[0]) is None:
                    raise RequestError(400, 'Invalid limit')
                try:
                    limit = int(values[0])
                except ValueError:
                    raise RequestError(400, 'Invalid limit')
                if not 1 <= limit <= 100:
                    raise RequestError(400, 'Invalid limit')
                self.require_inbox(db, inbox_id)
                rows = db.execute('''SELECT id,inbox_id,external_id,received_at,body_sha256
                    FROM events WHERE inbox_id=? ORDER BY received_at DESC,id DESC LIMIT ?''',
                                  (inbox_id, limit)).fetchall()
                return 200, {'events': [dict(row) for row in rows]}
            if action == 'retention':
                self.require_method('GET', 'PUT')
                if self.command == 'GET':
                    self.require_inbox(db, inbox_id)
                    row = db.execute('SELECT max_age_seconds FROM retention_policies WHERE inbox_id=?', (inbox_id,)).fetchone()
                    return 200, {'max_age_seconds': row[0] if row else None}
                value = self.json_object(text)
                if set(value) != {'max_age_seconds'}:
                    raise RequestError(400, 'Invalid retention policy')
                age = value['max_age_seconds']
                if age is not None and (type(age) is not int or not 1 <= age <= 2592000):
                    raise RequestError(400, 'Invalid retention policy')
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
                value = {} if text == '' else self.json_object(text)
                limit = value.get('limit', 100)
                if set(value) - {'limit'} or type(limit) is not int or not 1 <= limit <= 100:
                    raise RequestError(400, 'Invalid limit')
                db.execute('BEGIN IMMEDIATE')
                self.require_inbox(db, inbox_id)
                batch_time = datetime.now(timezone.utc)
                policy = db.execute('SELECT max_age_seconds FROM retention_policies WHERE inbox_id=?', (inbox_id,)).fetchone()
                deleted = 0
                if policy is not None:
                    cutoff = batch_time - timedelta(seconds=policy[0])
                    eligible = []
                    for row in db.execute('SELECT id,received_at FROM events WHERE inbox_id=?', (inbox_id,)):
                        instant = parse_instant(row['received_at'])
                        if instant <= cutoff:
                            eligible.append((instant, row['id']))
                    eligible.sort()
                    for instant, event_id in eligible[:limit]:
                        cursor = db.execute('DELETE FROM events WHERE id=? AND inbox_id=?', (event_id, inbox_id))
                        deleted += cursor.rowcount
                db.commit()
                return 200, {'deleted': deleted}
        if len(parts) == 4 and parts[1:3] == ['api', 'events'] and parts[3]:
            self.require_method('GET')
            row = db.execute('''SELECT id,inbox_id,external_id,received_at,body_sha256,body,headers_json
                FROM events WHERE id=?''', (parts[3],)).fetchone()
            if row is None:
                raise RequestError(404, 'Not found')
            value = dict(row)
            value['headers'] = json.loads(value.pop('headers_json'))
            return 200, value
        raise RequestError(404, 'Not found')

    def handle_request(self):
        db = None
        try:
            parsed = urlsplit(self.path)
            path = unquote(parsed.path)
            if path.startswith('/api/'):
                supplied = self.headers.get_all('Authorization', [])
                expected = ('Bearer ' + ADMIN_TOKEN).encode('utf-8')
                if len(supplied) != 1 or not hmac.compare_digest(supplied[0].encode('utf-8'), expected):
                    raise RequestError(401, 'Unauthorized')
            raw, text = self.read_body()
            db = connect()
            status, value = self.dispatch(db, path, parsed.query, raw, text)
            self.send_json(status, value)
        except RequestError as exc:
            self.close_connection = True
            self.send_json(exc.status, {'error': exc.message})
        except (BrokenPipeError, ConnectionResetError):
            self.close_connection = True
        except Exception:
            self.close_connection = True
            self.send_json(500, {'error': 'Internal server error'})
        finally:
            if db is not None:
                db.close()

    do_GET = handle_request
    do_POST = handle_request
    do_PUT = handle_request
    do_DELETE = handle_request
    do_PATCH = handle_request
    do_OPTIONS = handle_request
    do_HEAD = handle_request
    do_TRACE = handle_request
    do_CONNECT = handle_request


if __name__ == '__main__':
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
