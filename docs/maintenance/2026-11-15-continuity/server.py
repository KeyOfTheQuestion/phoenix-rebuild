import os
import json
import re
import hmac
import hashlib
import secrets
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
            raise RuntimeError('Unsupported schema version')
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


def timestamp():
    return datetime.now(timezone.utc).isoformat(timespec='microseconds')


def instant(value):
    if value.endswith('Z'):
        value = value[:-1] + '+00:00'
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


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
            return self.handle_request
        raise AttributeError(name)

    def send_json(self, status, payload):
        data = json.dumps(payload, ensure_ascii=True, separators=(',', ':')).encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(data)))
        self.send_header('Connection', 'close')
        self.end_headers()
        self.close_connection = True
        if self.command != 'HEAD':
            self.wfile.write(data)

    def read_body(self):
        if self.headers.get_all('Transfer-Encoding') is not None:
            raise HTTPError(400, 'Transfer-Encoding is not supported')
        lengths = self.headers.get_all('Content-Length')
        if lengths is None:
            if self.command in ('POST', 'GET', 'HEAD', 'DELETE', 'OPTIONS'):
                length = 0
            else:
                raise HTTPError(400, 'Missing Content-Length')
        else:
            if len(lengths) != 1 or re.fullmatch(r'[0-9]+', lengths[0].strip()) is None:
                raise HTTPError(400, 'Invalid Content-Length')
            number = lengths[0].strip().lstrip('0') or '0'
            if len(number) > 5 or int(number) > MAX_BODY:
                raise HTTPError(413, 'Body too large')
            length = int(number)
        raw = self.rfile.read(length)
        if len(raw) != length:
            raise HTTPError(400, 'Incomplete body')
        try:
            return raw.decode('utf-8')
        except UnicodeDecodeError:
            raise HTTPError(400, 'Invalid UTF-8')

    def json_object(self, body):
        def reject_constant(value):
            raise ValueError('Invalid JSON constant')
        try:
            result = json.loads(body, parse_constant=reject_constant)
        except (ValueError, RecursionError):
            raise HTTPError(400, 'Invalid JSON')
        if not isinstance(result, dict):
            raise HTTPError(400, 'Expected a JSON object')
        return result

    def handle_request(self):
        db = None
        try:
            parsed = urlsplit(self.path)
            path = parsed.path
            if path.startswith('/api/'):
                auth = self.headers.get_all('Authorization') or []
                expected = ('Bearer ' + ADMIN_TOKEN).encode('utf-8')
                if len(auth) != 1 or not hmac.compare_digest(auth[0].encode('utf-8'), expected):
                    raise HTTPError(401, 'Unauthorized')
            body = self.read_body()
            if path == '/health':
                self.require_method('GET')
                self.send_json(200, {'status': 'ok', 'schema_version': 2})
                return
            segments = path.split('/')
            route = None
            inbox_id = None
            event_id = None
            token = None
            if path == '/api/inboxes':
                route = 'inboxes'
                self.require_method('GET', 'POST')
            elif len(segments) == 5 and segments[1:3] == ['api', 'inboxes'] and segments[3] and segments[4] in ('events', 'retention', 'expire'):
                route = segments[4]
                inbox_id = unquote(segments[3])
                allowed = {'events': ('GET',), 'retention': ('GET', 'PUT'), 'expire': ('POST',)}
                self.require_method(*allowed[route])
            elif len(segments) == 4 and segments[1:3] == ['api', 'events'] and segments[3]:
                route = 'event'
                event_id = unquote(segments[3])
                self.require_method('GET')
            elif len(segments) == 4 and segments[1] == 'hooks' and segments[2] and segments[3]:
                route = 'hook'
                inbox_id = unquote(segments[2])
                token = unquote(segments[3])
                self.require_method('POST')
            else:
                raise HTTPError(404, 'Not found')
            db = connect()
            if route == 'inboxes':
                if self.command == 'GET':
                    rows = db.execute('SELECT id, name, created_at FROM inboxes ORDER BY created_at DESC, id DESC').fetchall()
                    self.send_json(200, {'inboxes': [dict(row) for row in rows]})
                else:
                    data = self.json_object(body)
                    name = data.get('name')
                    if not isinstance(name, str) or not 1 <= len(name.strip()) <= 80:
                        raise HTTPError(400, 'Invalid name')
                    name = name.strip()
                    new_token = secrets.token_urlsafe(32)
                    item = {'id': secrets.token_hex(16), 'name': name, 'created_at': timestamp(), 'token': new_token}
                    digest = hashlib.sha256(new_token.encode('utf-8')).hexdigest()
                    db.execute('INSERT INTO inboxes (id, name, token_sha256, created_at) VALUES (?, ?, ?, ?)', (item['id'], name, digest, item['created_at']))
                    db.commit()
                    self.send_json(201, item)
                return
            if route == 'event':
                row = db.execute('SELECT id, inbox_id, external_id, received_at, body_sha256, body, headers_json FROM events WHERE id=?', (event_id,)).fetchone()
                if row is None:
                    raise HTTPError(404, 'Not found')
                result = dict(row)
                result['headers'] = json.loads(result.pop('headers_json'))
                self.send_json(200, result)
                return
            if route in ('hook', 'expire') or (route == 'retention' and self.command == 'PUT'):
                db.execute('BEGIN IMMEDIATE')
            inbox = db.execute('SELECT id, token_sha256 FROM inboxes WHERE id=?', (inbox_id,)).fetchone()
            if inbox is None:
                raise HTTPError(404, 'Not found')
            if route == 'hook':
                digest = hashlib.sha256(token.encode('utf-8')).hexdigest()
                if not hmac.compare_digest(digest, inbox['token_sha256']):
                    raise HTTPError(404, 'Not found')
                external_id = self.headers.get('X-Webhook-Id') or None
                body_hash = hashlib.sha256(body.encode('utf-8')).hexdigest()
                if external_id is not None:
                    old = db.execute('SELECT id, body FROM events WHERE inbox_id=? AND external_id=?', (inbox_id, external_id)).fetchone()
                    if old is not None:
                        if old['body'] != body:
                            raise HTTPError(409, 'Idempotency key has a different body')
                        db.commit()
                        self.send_json(200, {'id': old['id'], 'duplicate': True})
                        return
                headers = {}
                for key in ('content-type', 'x-webhook-id', 'x-event-type'):
                    value = self.headers.get(key)
                    if value is not None:
                        headers[key] = value
                new_id = secrets.token_hex(16)
                db.execute('INSERT INTO events (id, inbox_id, external_id, received_at, headers_json, body, body_sha256) VALUES (?, ?, ?, ?, ?, ?, ?)', (new_id, inbox_id, external_id, timestamp(), json.dumps(headers), body, body_hash))
                db.commit()
                self.send_json(201, {'id': new_id, 'duplicate': False})
            elif route == 'events':
                query = parse_qs(parsed.query, keep_blank_values=True)
                values = query.get('limit', ['20'])
                if len(values) != 1 or re.fullmatch(r'[0-9]+', values[0]) is None:
                    raise HTTPError(400, 'Invalid limit')
                number = values[0].lstrip('0') or '0'
                if len(number) > 3 or not 1 <= int(number) <= 100:
                    raise HTTPError(400, 'Invalid limit')
                rows = db.execute('SELECT id, inbox_id, external_id, received_at, body_sha256 FROM events WHERE inbox_id=? ORDER BY received_at DESC, id DESC LIMIT ?', (inbox_id, int(number))).fetchall()
                self.send_json(200, {'events': [dict(row) for row in rows]})
            elif route == 'retention':
                if self.command == 'PUT':
                    data = self.json_object(body)
                    if set(data) != {'max_age_seconds'}:
                        raise HTTPError(400, 'Invalid retention policy')
                    age = data['max_age_seconds']
                    if age is not None and (type(age) is not int or not 1 <= age <= 2592000):
                        raise HTTPError(400, 'Invalid retention policy')
                    if age is None:
                        db.execute('DELETE FROM retention_policies WHERE inbox_id=?', (inbox_id,))
                    else:
                        db.execute('INSERT INTO retention_policies (inbox_id, max_age_seconds) VALUES (?, ?) ON CONFLICT(inbox_id) DO UPDATE SET max_age_seconds=excluded.max_age_seconds', (inbox_id, age))
                    db.commit()
                else:
                    policy = db.execute('SELECT max_age_seconds FROM retention_policies WHERE inbox_id=?', (inbox_id,)).fetchone()
                    age = None if policy is None else policy['max_age_seconds']
                self.send_json(200, {'max_age_seconds': age})
            elif route == 'expire':
                data = {} if body == '' else self.json_object(body)
                if set(data) - {'limit'}:
                    raise HTTPError(400, 'Invalid expiration request')
                limit = data.get('limit', 100)
                if type(limit) is not int or not 1 <= limit <= 100:
                    raise HTTPError(400, 'Invalid limit')
                now = datetime.now(timezone.utc)
                policy = db.execute('SELECT max_age_seconds FROM retention_policies WHERE inbox_id=?', (inbox_id,)).fetchone()
                deleted = 0
                if policy is not None:
                    cutoff = now - timedelta(seconds=policy['max_age_seconds'])
                    eligible = []
                    for row in db.execute('SELECT id, received_at FROM events WHERE inbox_id=?', (inbox_id,)):
                        received = instant(row['received_at'])
                        if received <= cutoff:
                            eligible.append((received, row['id']))
                    eligible.sort()
                    for received, selected_id in eligible[:limit]:
                        cursor = db.execute('DELETE FROM events WHERE inbox_id=? AND id=?', (inbox_id, selected_id))
                        deleted += cursor.rowcount
                db.commit()
                self.send_json(200, {'deleted': deleted})
        except HTTPError as exc:
            self.send_json(exc.status, {'error': exc.message})
        except (BrokenPipeError, ConnectionResetError):
            self.close_connection = True
        except Exception:
            self.send_json(500, {'error': 'Internal server error'})
        finally:
            if db is not None:
                db.close()

    def require_method(self, *methods):
        if self.command not in methods:
            raise HTTPError(405, 'Method not allowed')

    def send_error(self, code, message=None, explain=None):
        self.send_json(code, {'error': 'Invalid HTTP request'})


class Server(ThreadingHTTPServer):
    daemon_threads = True

    def handle_error(self, request, client_address):
        pass


def main():
    if not ADMIN_TOKEN:
        raise SystemExit('PHOENIX_ADMIN_TOKEN is required')
    try:
        port = int(os.environ.get('PORT', '8787'))
        initialize()
        server = Server(('127.0.0.1', port), Handler)
    except Exception:
        raise SystemExit('Service startup failed')
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == '__main__':
    main()
