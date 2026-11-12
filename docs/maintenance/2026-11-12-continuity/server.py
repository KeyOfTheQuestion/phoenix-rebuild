import os
import json
import re
import hmac
import hashlib
import secrets
import sqlite3
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
            raise RuntimeError('Unsupported database version')
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


class RequestError(Exception):
    def __init__(self, status, message):
        self.status = status
        self.message = message


class Handler(BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'

    def log_message(self, *args):
        pass

    def log_error(self, *args):
        pass

    def send_json(self, status, payload):
        data = json.dumps(payload, ensure_ascii=True, separators=(',', ':')).encode('utf-8')
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
        self.send_json(code, {'error': 'Request rejected'})

    def handle_expect_100(self):
        return True

    def read_body(self):
        if self.headers.get_all('Transfer-Encoding') is not None:
            raise RequestError(400, 'Transfer-Encoding is unsupported')
        lengths = self.headers.get_all('Content-Length', [])
        if not lengths:
            if self.command in ('POST', 'GET', 'HEAD', 'DELETE', 'OPTIONS'):
                length = 0
            else:
                raise RequestError(400, 'Missing Content-Length')
        else:
            if len(lengths) != 1 or re.fullmatch(r'[0-9]+', lengths[0]) is None:
                raise RequestError(400, 'Invalid Content-Length')
            try:
                length = int(lengths[0])
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

    def parse_json(self, text):
        try:
            return json.loads(text, parse_constant=lambda value: self.invalid_json())
        except (ValueError, RecursionError):
            raise RequestError(400, 'Invalid JSON')

    def invalid_json(self):
        raise ValueError('Invalid constant')

    def require_inbox(self, db, inbox_id):
        row = db.execute('SELECT id FROM inboxes WHERE id=?', (inbox_id,)).fetchone()
        if row is None:
            raise RequestError(404, 'Inbox not found')

    def dispatch(self):
        db = None
        try:
            parsed = urlsplit(self.path)
            path = parsed.path
            parts = [unquote(part) for part in path.split('/')]
            if path.startswith('/api/') or path == '/api':
                auth = self.headers.get_all('Authorization', [])
                expected = ('Bearer ' + ADMIN_TOKEN).encode('utf-8')
                if len(auth) != 1 or not hmac.compare_digest(auth[0].encode('utf-8'), expected):
                    raise RequestError(401, 'Unauthorized')
            raw, body = self.read_body()
            route = None
            allowed = ()
            if path == '/health':
                route, allowed = 'health', ('GET',)
            elif path == '/api/inboxes':
                route, allowed = 'inboxes', ('GET', 'POST')
            elif len(parts) == 4 and parts[1] == 'hooks' and parts[2] and parts[3]:
                route, allowed = 'hook', ('POST',)
            elif len(parts) == 5 and parts[1:3] == ['api', 'inboxes'] and parts[3]:
                if parts[4] == 'events':
                    route, allowed = 'events', ('GET',)
                elif parts[4] == 'retention':
                    route, allowed = 'retention', ('GET', 'PUT')
                elif parts[4] == 'expire':
                    route, allowed = 'expire', ('POST',)
            elif len(parts) == 4 and parts[1:3] == ['api', 'events'] and parts[3]:
                route, allowed = 'event', ('GET',)
            if route is None:
                raise RequestError(404, 'Not found')
            if self.command not in allowed:
                raise RequestError(405, 'Method not allowed')
            if route == 'health':
                self.send_json(200, {'status': 'ok', 'schema_version': 2})
                return
            db = connect()
            if route == 'inboxes':
                if self.command == 'GET':
                    rows = db.execute('SELECT id,name,created_at FROM inboxes ORDER BY created_at DESC,id DESC').fetchall()
                    self.send_json(200, {'inboxes': [dict(row) for row in rows]})
                else:
                    data = self.parse_json(body)
                    if not isinstance(data, dict) or not isinstance(data.get('name'), str):
                        raise RequestError(400, 'Invalid name')
                    name = data['name'].strip()
                    if not 1 <= len(name) <= 80:
                        raise RequestError(400, 'Invalid name')
                    token = secrets.token_urlsafe(32)
                    result = {'id': secrets.token_hex(16), 'name': name, 'created_at': now_text(), 'token': token}
                    db.execute('BEGIN IMMEDIATE')
                    db.execute('INSERT INTO inboxes (id,name,token_sha256,created_at) VALUES (?,?,?,?)', (result['id'], name, digest(token.encode('utf-8')), result['created_at']))
                    db.commit()
                    self.send_json(201, result)
            elif route == 'hook':
                inbox_id, token = parts[2:4]
                db.execute('BEGIN IMMEDIATE')
                inbox = db.execute('SELECT token_sha256 FROM inboxes WHERE id=?', (inbox_id,)).fetchone()
                if inbox is None or not hmac.compare_digest(inbox['token_sha256'], digest(token.encode('utf-8'))):
                    raise RequestError(404, 'Not found')
                external = self.headers.get('X-Webhook-Id') or None
                body_hash = digest(raw)
                if external is not None:
                    existing = db.execute('SELECT id,body_sha256,body FROM events WHERE inbox_id=? AND external_id=?', (inbox_id, external)).fetchone()
                    if existing is not None:
                        if existing['body_sha256'] != body_hash or existing['body'] != body:
                            raise RequestError(409, 'Idempotency conflict')
                        db.commit()
                        self.send_json(200, {'id': existing['id'], 'duplicate': True})
                        return
                headers = {}
                for key in ('content-type', 'x-webhook-id', 'x-event-type'):
                    value = self.headers.get(key)
                    if value is not None:
                        headers[key] = value
                event_id = secrets.token_hex(16)
                db.execute('INSERT INTO events (id,inbox_id,external_id,received_at,headers_json,body,body_sha256) VALUES (?,?,?,?,?,?,?)', (event_id, inbox_id, external, now_text(), json.dumps(headers), body, body_hash))
                db.commit()
                self.send_json(201, {'id': event_id, 'duplicate': False})
            elif route == 'event':
                row = db.execute('SELECT id,inbox_id,external_id,received_at,body_sha256,body,headers_json FROM events WHERE id=?', (parts[3],)).fetchone()
                if row is None:
                    raise RequestError(404, 'Event not found')
                result = dict(row)
                result['headers'] = json.loads(result.pop('headers_json'))
                self.send_json(200, result)
            elif route == 'events':
                self.require_inbox(db, parts[3])
                query = parse_qs(parsed.query, keep_blank_values=True)
                values = query.get('limit', ['20'])
                if len(values) != 1 or re.fullmatch(r'[0-9]+', values[0]) is None:
                    raise RequestError(400, 'Invalid limit')
                try:
                    limit = int(values[0])
                except ValueError:
                    raise RequestError(400, 'Invalid limit')
                if not 1 <= limit <= 100:
                    raise RequestError(400, 'Invalid limit')
                rows = db.execute('SELECT id,inbox_id,external_id,received_at,body_sha256 FROM events WHERE inbox_id=? ORDER BY received_at DESC,id DESC LIMIT ?', (parts[3], limit)).fetchall()
                self.send_json(200, {'events': [dict(row) for row in rows]})
            elif route == 'retention':
                inbox_id = parts[3]
                if self.command == 'PUT':
                    data = self.parse_json(body)
                    if not isinstance(data, dict) or set(data) != {'max_age_seconds'}:
                        raise RequestError(400, 'Invalid policy')
                    age = data['max_age_seconds']
                    if age is not None and (type(age) is not int or not 1 <= age <= 2592000):
                        raise RequestError(400, 'Invalid policy')
                    db.execute('BEGIN IMMEDIATE')
                    self.require_inbox(db, inbox_id)
                    if age is None:
                        db.execute('DELETE FROM retention_policies WHERE inbox_id=?', (inbox_id,))
                    else:
                        db.execute('INSERT INTO retention_policies (inbox_id,max_age_seconds) VALUES (?,?) ON CONFLICT(inbox_id) DO UPDATE SET max_age_seconds=excluded.max_age_seconds', (inbox_id, age))
                    db.commit()
                else:
                    self.require_inbox(db, inbox_id)
                    row = db.execute('SELECT max_age_seconds FROM retention_policies WHERE inbox_id=?', (inbox_id,)).fetchone()
                    age = row[0] if row is not None else None
                self.send_json(200, {'max_age_seconds': age})
            elif route == 'expire':
                data = {} if body == '' else self.parse_json(body)
                if not isinstance(data, dict) or set(data) - {'limit'}:
                    raise RequestError(400, 'Invalid expiration request')
                limit = data.get('limit', 100)
                if type(limit) is not int or not 1 <= limit <= 100:
                    raise RequestError(400, 'Invalid limit')
                db.execute('BEGIN IMMEDIATE')
                self.require_inbox(db, parts[3])
                current = datetime.now(timezone.utc)
                policy = db.execute('SELECT max_age_seconds FROM retention_policies WHERE inbox_id=?', (parts[3],)).fetchone()
                deleted = 0
                if policy is not None:
                    cutoff = current - timedelta(seconds=policy[0])
                    eligible = []
                    for row in db.execute('SELECT id,received_at FROM events WHERE inbox_id=?', (parts[3],)):
                        received = instant(row['received_at'])
                        if received <= cutoff:
                            eligible.append((received, row['id']))
                    eligible.sort()
                    for received, event_id in eligible[:limit]:
                        deleted += db.execute('DELETE FROM events WHERE inbox_id=? AND id=?', (parts[3], event_id)).rowcount
                db.commit()
                self.send_json(200, {'deleted': deleted})
        except RequestError as exc:
            if db is not None and db.in_transaction:
                db.rollback()
            self.close_connection = True
            self.send_json(exc.status, {'error': exc.message})
        except Exception:
            if db is not None and db.in_transaction:
                db.rollback()
            self.close_connection = True
            try:
                self.send_json(500, {'error': 'Internal server error'})
            except Exception:
                pass
        finally:
            if db is not None:
                db.close()

    def __getattr__(self, name):
        if name.startswith('do_'):
            return self.dispatch
        raise AttributeError(name)


class Server(ThreadingHTTPServer):
    daemon_threads = True

    def handle_error(self, request, client_address):
        pass


if __name__ == '__main__':
    if not ADMIN_TOKEN:
        raise SystemExit('PHOENIX_ADMIN_TOKEN is required')
    try:
        initialize()
        server = Server(('127.0.0.1', int(os.environ.get('PORT', '8787'))), Handler)
    except Exception:
        raise SystemExit('Unable to initialize service')
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
