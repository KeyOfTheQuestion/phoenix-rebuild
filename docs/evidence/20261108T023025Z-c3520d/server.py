import os
import json
import sqlite3
import secrets
import hashlib
import hmac
from datetime import datetime, timezone, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit, parse_qs

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
            raise RuntimeError('Unsupported schema version')
        if version in (0, 1):
            db.execute('CREATE TABLE IF NOT EXISTS inboxes (id TEXT PRIMARY KEY, name TEXT NOT NULL, token_sha256 TEXT NOT NULL, created_at TEXT NOT NULL)')
            db.execute('CREATE TABLE IF NOT EXISTS events (id TEXT PRIMARY KEY, inbox_id TEXT NOT NULL REFERENCES inboxes(id), external_id TEXT, received_at TEXT NOT NULL, headers_json TEXT NOT NULL, body TEXT NOT NULL, body_sha256 TEXT NOT NULL, UNIQUE(inbox_id, external_id))')
            db.execute('CREATE TABLE IF NOT EXISTS retention_policies (inbox_id TEXT PRIMARY KEY REFERENCES inboxes(id), max_age_seconds INTEGER NOT NULL CHECK(max_age_seconds BETWEEN 1 AND 2592000))')
            db.execute('PRAGMA user_version=2')
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def timestamp():
    return datetime.now(timezone.utc).isoformat()


def instant(value):
    if value.endswith('Z'):
        value = value[:-1] + '+00:00'
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def summary(row):
    return {key: row[key] for key in ('id', 'inbox_id', 'external_id', 'received_at', 'body_sha256')}


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
        self.send_json(code, {'error': 'Invalid HTTP request'})

    def handle_expect_100(self):
        if self.headers.get_all('Transfer-Encoding'):
            self.send_error(400)
            return False
        lengths = self.headers.get_all('Content-Length', [])
        if len(lengths) != 1 or not lengths[0].isascii() or not lengths[0].isdigit():
            self.send_error(400)
            return False
        if int(lengths[0]) > MAX_BODY:
            self.send_error(413)
            return False
        return super().handle_expect_100()

    def read_body(self):
        if self.headers.get_all('Transfer-Encoding'):
            raise HTTPError(400, 'Transfer-Encoding is unsupported')
        lengths = self.headers.get_all('Content-Length', [])
        if not lengths:
            if self.command in ('POST', 'GET', 'HEAD', 'DELETE', 'OPTIONS', 'PATCH', 'TRACE', 'CONNECT'):
                length = 0
            else:
                raise HTTPError(400, 'Missing Content-Length')
        else:
            if len(lengths) != 1 or not lengths[0].isascii() or not lengths[0].isdigit():
                raise HTTPError(400, 'Invalid Content-Length')
            try:
                length = int(lengths[0])
            except ValueError:
                raise HTTPError(400, 'Invalid Content-Length')
        if length > MAX_BODY:
            raise HTTPError(413, 'Body too large')
        raw = self.rfile.read(length)
        if len(raw) != length:
            raise HTTPError(400, 'Incomplete body')
        try:
            return raw.decode('utf-8'), raw
        except UnicodeDecodeError:
            raise HTTPError(400, 'Invalid UTF-8')

    def parse_json(self, body):
        def invalid_constant(value):
            raise ValueError('Invalid constant')
        try:
            return json.loads(body, parse_constant=invalid_constant)
        except (ValueError, RecursionError):
            raise HTTPError(400, 'Invalid JSON')

    def require_method(self, *methods):
        if self.command not in methods:
            raise HTTPError(405, 'Method not allowed')

    def require_inbox(self, db, inbox_id):
        row = db.execute('SELECT id FROM inboxes WHERE id=?', (inbox_id,)).fetchone()
        if row is None:
            raise HTTPError(404, 'Inbox not found')

    def dispatch(self):
        db = None
        try:
            parsed = urlsplit(self.path)
            path = parsed.path
            if path.startswith('/api/'):
                credentials = self.headers.get_all('Authorization', [])
                expected = ('Bearer ' + ADMIN_TOKEN).encode('utf-8')
                if len(credentials) != 1 or not hmac.compare_digest(credentials[0].encode('utf-8'), expected):
                    raise HTTPError(401, 'Unauthorized')
            body, raw = self.read_body()
            parts = path.split('/')
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
                else:
                    value = self.parse_json(body)
                    if not isinstance(value, dict) or not isinstance(value.get('name'), str):
                        raise HTTPError(400, 'Invalid name')
                    name = value['name'].strip()
                    if not 1 <= len(name) <= 80:
                        raise HTTPError(400, 'Invalid name')
                    token = secrets.token_urlsafe(32)
                    inbox_id = secrets.token_urlsafe(24)
                    created_at = timestamp()
                    db.execute('INSERT INTO inboxes (id,name,token_sha256,created_at) VALUES (?,?,?,?)', (inbox_id, name, hashlib.sha256(token.encode()).hexdigest(), created_at))
                    db.commit()
                    self.send_json(201, {'id': inbox_id, 'name': name, 'created_at': created_at, 'token': token})
                return
            if len(parts) == 4 and parts[1] == 'hooks' and parts[2] and parts[3]:
                self.require_method('POST')
                inbox_id, token = parts[2:]
                db.execute('BEGIN IMMEDIATE')
                inbox = db.execute('SELECT token_sha256 FROM inboxes WHERE id=?', (inbox_id,)).fetchone()
                digest = hashlib.sha256(token.encode('utf-8')).hexdigest()
                if inbox is None or not hmac.compare_digest(inbox['token_sha256'], digest):
                    raise HTTPError(404, 'Inbox not found')
                external_id = self.headers.get('X-Webhook-Id') or None
                body_hash = hashlib.sha256(raw).hexdigest()
                if external_id is not None:
                    old = db.execute('SELECT id,body FROM events WHERE inbox_id=? AND external_id=?', (inbox_id, external_id)).fetchone()
                    if old is not None:
                        if old['body'] != body:
                            raise HTTPError(409, 'Idempotency key conflicts with body')
                        db.commit()
                        self.send_json(200, {'id': old['id'], 'duplicate': True})
                        return
                headers = {}
                for key in ('content-type', 'x-webhook-id', 'x-event-type'):
                    if self.headers.get(key) is not None:
                        headers[key] = self.headers.get(key)
                event_id = secrets.token_urlsafe(24)
                db.execute('INSERT INTO events (id,inbox_id,external_id,received_at,headers_json,body,body_sha256) VALUES (?,?,?,?,?,?,?)', (event_id, inbox_id, external_id, timestamp(), json.dumps(headers), body, body_hash))
                db.commit()
                self.send_json(201, {'id': event_id, 'duplicate': False})
                return
            if len(parts) == 5 and parts[1:3] == ['api', 'inboxes'] and parts[3]:
                inbox_id, operation = parts[3:]
                if operation == 'events':
                    self.require_method('GET')
                    query = parse_qs(parsed.query, keep_blank_values=True)
                    values = query.get('limit', ['20'])
                    if len(values) != 1 or not values[0].isascii() or not values[0].isdigit():
                        raise HTTPError(400, 'Invalid limit')
                    try:
                        limit = int(values[0])
                    except ValueError:
                        raise HTTPError(400, 'Invalid limit')
                    if not 1 <= limit <= 100:
                        raise HTTPError(400, 'Invalid limit')
                    self.require_inbox(db, inbox_id)
                    rows = db.execute('SELECT id,inbox_id,external_id,received_at,body_sha256 FROM events WHERE inbox_id=? ORDER BY received_at DESC,id DESC LIMIT ?', (inbox_id, limit)).fetchall()
                    self.send_json(200, {'events': [summary(row) for row in rows]})
                    return
                if operation == 'retention':
                    self.require_method('GET', 'PUT')
                    if self.command == 'GET':
                        self.require_inbox(db, inbox_id)
                        row = db.execute('SELECT max_age_seconds FROM retention_policies WHERE inbox_id=?', (inbox_id,)).fetchone()
                        self.send_json(200, {'max_age_seconds': row[0] if row else None})
                        return
                    value = self.parse_json(body)
                    if not isinstance(value, dict) or set(value) != {'max_age_seconds'}:
                        raise HTTPError(400, 'Invalid retention policy')
                    age = value['max_age_seconds']
                    if age is not None and (type(age) is not int or not 1 <= age <= 2592000):
                        raise HTTPError(400, 'Invalid retention policy')
                    db.execute('BEGIN IMMEDIATE')
                    self.require_inbox(db, inbox_id)
                    if age is None:
                        db.execute('DELETE FROM retention_policies WHERE inbox_id=?', (inbox_id,))
                    else:
                        db.execute('INSERT INTO retention_policies (inbox_id,max_age_seconds) VALUES (?,?) ON CONFLICT(inbox_id) DO UPDATE SET max_age_seconds=excluded.max_age_seconds', (inbox_id, age))
                    db.commit()
                    self.send_json(200, {'max_age_seconds': age})
                    return
                if operation == 'expire':
                    self.require_method('POST')
                    value = {} if body == '' else self.parse_json(body)
                    if not isinstance(value, dict) or set(value) - {'limit'}:
                        raise HTTPError(400, 'Invalid expiry request')
                    limit = value.get('limit', 100)
                    if type(limit) is not int or not 1 <= limit <= 100:
                        raise HTTPError(400, 'Invalid limit')
                    db.execute('BEGIN IMMEDIATE')
                    self.require_inbox(db, inbox_id)
                    now = datetime.now(timezone.utc)
                    policy = db.execute('SELECT max_age_seconds FROM retention_policies WHERE inbox_id=?', (inbox_id,)).fetchone()
                    deleted = 0
                    if policy is not None:
                        cutoff = now - timedelta(seconds=policy[0])
                        eligible = []
                        for row in db.execute('SELECT id,received_at FROM events WHERE inbox_id=?', (inbox_id,)):
                            received = instant(row['received_at'])
                            if received <= cutoff:
                                eligible.append((received, row['id']))
                        eligible.sort()
                        for received, event_id in eligible[:limit]:
                            cursor = db.execute('DELETE FROM events WHERE id=? AND inbox_id=?', (event_id, inbox_id))
                            deleted += cursor.rowcount
                    db.commit()
                    self.send_json(200, {'deleted': deleted})
                    return
            if len(parts) == 4 and parts[1:3] == ['api', 'events'] and parts[3]:
                self.require_method('GET')
                row = db.execute('SELECT id,inbox_id,external_id,received_at,body_sha256,body,headers_json FROM events WHERE id=?', (parts[3],)).fetchone()
                if row is None:
                    raise HTTPError(404, 'Event not found')
                value = summary(row)
                value['body'] = row['body']
                value['headers'] = json.loads(row['headers_json'])
                self.send_json(200, value)
                return
            if self.command not in ('GET', 'POST', 'PUT'):
                raise HTTPError(405, 'Method not allowed')
            raise HTTPError(404, 'Not found')
        except HTTPError as exc:
            self.close_connection = True
            self.send_json(exc.status, {'error': exc.message})
        except (ValueError, UnicodeError):
            self.close_connection = True
            self.send_json(400, {'error': 'Invalid request'})
        except Exception:
            self.close_connection = True
            try:
                self.send_json(500, {'error': 'Internal server error'})
            except OSError:
                pass
        finally:
            if db is not None:
                db.close()


def main():
    if not ADMIN_TOKEN:
        raise SystemExit('PHOENIX_ADMIN_TOKEN is required')
    try:
        initialize()
        server = ThreadingHTTPServer(('127.0.0.1', int(os.environ.get('PORT', '8787'))), Handler)
    except Exception:
        raise SystemExit('Unable to initialize service')
    server.daemon_threads = True
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == '__main__':
    main()
