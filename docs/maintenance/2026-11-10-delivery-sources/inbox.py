import os
import sys
import json
import sqlite3
import secrets
import hashlib
import hmac
import re
from datetime import datetime, timezone, timedelta
from urllib.parse import urlsplit, unquote, parse_qs
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

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
    return datetime.now(timezone.utc).isoformat()


def instant(value):
    if value.endswith('Z'):
        value = value[:-1] + '+00:00'
    result = datetime.fromisoformat(value)
    if result.tzinfo is None:
        result = result.replace(tzinfo=timezone.utc)
    return result.astimezone(timezone.utc)


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('Duplicate JSON key')
        result[key] = value
    return result


def bad_constant(value):
    raise ValueError('Invalid JSON constant')


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

    def send_json(self, status, data):
        payload = json.dumps(data, ensure_ascii=True, separators=(',', ':')).encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(payload)))
        self.send_header('Connection', 'close')
        self.end_headers()
        self.close_connection = True
        self.wfile.write(payload)

    def send_error(self, code, message=None, explain=None):
        self.send_json(code, {'error': 'Bad request' if code == 400 else 'HTTP error'})

    def handle_expect_100(self):
        return True

    def read_body(self):
        if self.headers.get_all('Transfer-Encoding') is not None:
            raise HTTPError(400, 'Transfer-Encoding is unsupported')
        lengths = self.headers.get_all('Content-Length', [])
        if len(lengths) > 1:
            raise HTTPError(400, 'Invalid Content-Length')
        if not lengths:
            if self.command in ('PUT', 'PATCH'):
                raise HTTPError(400, 'Missing Content-Length')
            return ''
        length_text = lengths[0].strip()
        if re.fullmatch(r'[0-9]+', length_text) is None:
            raise HTTPError(400, 'Invalid Content-Length')
        try:
            length = int(length_text)
        except ValueError:
            raise HTTPError(400, 'Invalid Content-Length')
        if length > MAX_BODY:
            raise HTTPError(413, 'Body too large')
        if self.headers.get('Expect', '').lower() == '100-continue':
            self.send_response_only(100)
            self.end_headers()
        raw = self.rfile.read(length)
        if len(raw) != length:
            raise HTTPError(400, 'Incomplete body')
        try:
            return raw.decode('utf-8')
        except UnicodeDecodeError:
            raise HTTPError(400, 'Invalid UTF-8')

    def parse_json(self, body):
        try:
            return json.loads(body, object_pairs_hook=unique_object, parse_constant=bad_constant)
        except (ValueError, RecursionError):
            raise HTTPError(400, 'Invalid JSON')

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
            parsed = urlsplit(self.path)
            path = parsed.path
            if path.startswith('/api/') or path == '/api':
                auth = self.headers.get_all('Authorization', [])
                expected = ('Bearer ' + ADMIN_TOKEN).encode('utf-8')
                if len(auth) != 1 or not hmac.compare_digest(auth[0].encode('utf-8'), expected):
                    raise HTTPError(401, 'Unauthorized')
            body = self.read_body()
            parts = [unquote(part) for part in path.split('/')]
            if path == '/health':
                self.require_method('GET')
                self.send_json(200, {'status': 'ok', 'schema_version': 2})
                return
            db = connect()
            status, result = self.dispatch(db, path, parts, parsed.query, body)
            self.send_json(status, result)
        except HTTPError as exc:
            self.send_json(exc.status, {'error': exc.message})
        except (BrokenPipeError, ConnectionResetError):
            self.close_connection = True
        except Exception:
            self.send_json(500, {'error': 'Internal server error'})
        finally:
            if db is not None:
                if db.in_transaction:
                    db.rollback()
                db.close()

    def dispatch(self, db, path, parts, query, body):
        if path == '/api/inboxes':
            self.require_method('GET', 'POST')
            if self.command == 'GET':
                rows = db.execute('SELECT id,name,created_at FROM inboxes ORDER BY created_at DESC,id DESC').fetchall()
                return 200, {'inboxes': [dict(row) for row in rows]}
            data = self.parse_json(body)
            if not isinstance(data, dict) or not isinstance(data.get('name'), str):
                raise HTTPError(400, 'Invalid name')
            name = data['name'].strip()
            if not 1 <= len(name) <= 80:
                raise HTTPError(400, 'Invalid name')
            inbox_id = secrets.token_urlsafe(24)
            token = secrets.token_urlsafe(32)
            created = timestamp()
            digest = hashlib.sha256(token.encode('utf-8')).hexdigest()
            db.execute('BEGIN IMMEDIATE')
            db.execute('INSERT INTO inboxes(id,name,token_sha256,created_at) VALUES(?,?,?,?)', (inbox_id, name, digest, created))
            db.commit()
            return 201, {'id': inbox_id, 'name': name, 'created_at': created, 'token': token}

        if len(parts) == 4 and parts[1] == 'hooks' and parts[2] and parts[3]:
            self.require_method('POST')
            inbox_id, token = parts[2:4]
            db.execute('BEGIN IMMEDIATE')
            inbox = db.execute('SELECT token_sha256 FROM inboxes WHERE id=?', (inbox_id,)).fetchone()
            digest = hashlib.sha256(token.encode('utf-8')).hexdigest()
            if inbox is None or not hmac.compare_digest(inbox['token_sha256'], digest):
                raise HTTPError(404, 'Not found')
            headers = {}
            for key in ('content-type', 'x-webhook-id', 'x-event-type'):
                value = self.headers.get(key)
                if value is not None:
                    headers[key] = value
            external_id = headers.get('x-webhook-id') or None
            body_hash = hashlib.sha256(body.encode('utf-8')).hexdigest()
            if external_id is not None:
                old = db.execute('SELECT id,body FROM events WHERE inbox_id=? AND external_id=?', (inbox_id, external_id)).fetchone()
                if old is not None:
                    if old['body'] != body:
                        raise HTTPError(409, 'Idempotency key conflict')
                    db.commit()
                    return 200, {'id': old['id'], 'duplicate': True}
            event_id = secrets.token_urlsafe(24)
            db.execute('''INSERT INTO events(id,inbox_id,external_id,received_at,headers_json,body,body_sha256)
                          VALUES(?,?,?,?,?,?,?)''', (event_id, inbox_id, external_id, timestamp(), json.dumps(headers), body, body_hash))
            db.commit()
            return 201, {'id': event_id, 'duplicate': False}

        if len(parts) == 5 and parts[1:3] == ['api', 'inboxes'] and parts[3]:
            inbox_id = parts[3]
            action = parts[4]
            if action == 'events':
                self.require_method('GET')
                params = parse_qs(query, keep_blank_values=True)
                limits = params.get('limit', ['20'])
                if len(limits) != 1 or re.fullmatch(r'[0-9]+', limits[0]) is None:
                    raise HTTPError(400, 'Invalid limit')
                try:
                    limit = int(limits[0])
                except ValueError:
                    raise HTTPError(400, 'Invalid limit')
                if not 1 <= limit <= 100:
                    raise HTTPError(400, 'Invalid limit')
                self.require_inbox(db, inbox_id)
                rows = db.execute('''SELECT id,inbox_id,external_id,received_at,body_sha256 FROM events
                    WHERE inbox_id=? ORDER BY received_at DESC,id DESC LIMIT ?''', (inbox_id, limit)).fetchall()
                return 200, {'events': [dict(row) for row in rows]}
            if action == 'retention':
                self.require_method('GET', 'PUT')
                if self.command == 'GET':
                    self.require_inbox(db, inbox_id)
                    row = db.execute('SELECT max_age_seconds FROM retention_policies WHERE inbox_id=?', (inbox_id,)).fetchone()
                    return 200, {'max_age_seconds': row[0] if row is not None else None}
                data = self.parse_json(body)
                if not isinstance(data, dict) or set(data) != {'max_age_seconds'}:
                    raise HTTPError(400, 'Invalid retention policy')
                age = data['max_age_seconds']
                if age is not None and (type(age) is not int or not 1 <= age <= 2592000):
                    raise HTTPError(400, 'Invalid retention policy')
                db.execute('BEGIN IMMEDIATE')
                self.require_inbox(db, inbox_id)
                if age is None:
                    db.execute('DELETE FROM retention_policies WHERE inbox_id=?', (inbox_id,))
                else:
                    db.execute('''INSERT INTO retention_policies(inbox_id,max_age_seconds) VALUES(?,?)
                        ON CONFLICT(inbox_id) DO UPDATE SET max_age_seconds=excluded.max_age_seconds''', (inbox_id, age))
                db.commit()
                return 200, {'max_age_seconds': age}
            if action == 'expire':
                self.require_method('POST')
                data = {} if body == '' else self.parse_json(body)
                if not isinstance(data, dict) or not set(data).issubset({'limit'}):
                    raise HTTPError(400, 'Invalid expiration request')
                limit = data.get('limit', 100)
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
                return 200, {'deleted': deleted}

        if len(parts) == 4 and parts[1:3] == ['api', 'events'] and parts[3]:
            self.require_method('GET')
            row = db.execute('SELECT id,inbox_id,external_id,received_at,body_sha256,body,headers_json FROM events WHERE id=?', (parts[3],)).fetchone()
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
        sys.stderr.write('PHOENIX_ADMIN_TOKEN is required\n')
        return 1
    try:
        initialize()
        port = int(os.environ.get('PORT', '8787'))
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
