import os
import sys
import json
import uuid
import secrets
import hashlib
import hmac
import sqlite3
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit, parse_qs, unquote

MAX_BODY_BYTES = 65536
SCHEMA_VERSION = 1

ADMIN_TOKEN = os.environ.get("PHOENIX_ADMIN_TOKEN", "")
if not ADMIN_TOKEN:
    sys.stderr.write("PHOENIX_ADMIN_TOKEN is required\n")
    raise SystemExit(1)

DB_PATH = os.environ.get("PHOENIX_DB_PATH", "phoenix.sqlite")
try:
    PORT = int(os.environ.get("PORT", "8787"))
    if not 1 <= PORT <= 65535:
        raise ValueError
except ValueError:
    sys.stderr.write("PORT must be an integer from 1 to 65535\n")
    raise SystemExit(1)


def utc_now():
    return datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def new_id():
    return uuid.uuid4().hex


def connect_db():
    connection = sqlite3.connect(DB_PATH, timeout=10.0)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys=ON")
    connection.execute("PRAGMA busy_timeout=10000")
    return connection


def initialize_database():
    connection = connect_db()
    try:
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS inboxes (
              id TEXT PRIMARY KEY,
              name TEXT NOT NULL,
              token_sha256 TEXT NOT NULL,
              created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS events (
              id TEXT PRIMARY KEY,
              inbox_id TEXT NOT NULL REFERENCES inboxes(id),
              external_id TEXT,
              received_at TEXT NOT NULL,
              headers_json TEXT NOT NULL,
              body TEXT NOT NULL,
              body_sha256 TEXT NOT NULL,
              UNIQUE(inbox_id, external_id)
            );
            """
        )
        connection.commit()
    finally:
        connection.close()


try:
    initialize_database()
except Exception as exc:
    sys.stderr.write("Failed to initialize database: %s\n" % exc)
    raise SystemExit(1)


class PhoenixHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "Phoenix/1.0"
    sys_version = ""

    def log_message(self, format, *args):
        return

    def send_json(self, status, value, close=False, extra_headers=None):
        data = json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        if extra_headers:
            for name, header_value in extra_headers.items():
                self.send_header(name, header_value)
        if close:
            self.send_header("Connection", "close")
            self.close_connection = True
        self.end_headers()
        if self.command != "HEAD":
            try:
                self.wfile.write(data)
            except (BrokenPipeError, ConnectionResetError):
                pass

    def error_json(self, status, message, close=False, extra_headers=None):
        self.send_json(status, {"error": message}, close=close, extra_headers=extra_headers)

    def parsed_target(self):
        try:
            return urlsplit(self.path)
        except ValueError:
            return None

    def is_admin_authorized(self):
        values = self.headers.get_all("Authorization", [])
        if len(values) != 1:
            return False
        expected = "Bearer " + ADMIN_TOKEN
        return hmac.compare_digest(values[0], expected)

    def require_admin(self):
        if not self.is_admin_authorized():
            self.error_json(401, "unauthorized")
            return False
        return True

    def read_post_body(self):
        transfer_values = self.headers.get_all("Transfer-Encoding", [])
        if transfer_values:
            self.error_json(400, "transfer encoding is not supported", close=True)
            return None

        lengths = self.headers.get_all("Content-Length", [])
        if not lengths:
            length = 0
        elif len(lengths) != 1:
            self.error_json(400, "malformed Content-Length", close=True)
            return None
        else:
            raw_length = lengths[0]
            if not raw_length or not raw_length.isascii() or not raw_length.isdecimal():
                self.error_json(400, "malformed Content-Length", close=True)
                return None
            try:
                length = int(raw_length, 10)
            except ValueError:
                self.error_json(400, "malformed Content-Length", close=True)
                return None

        if length < 0:
            self.error_json(400, "negative Content-Length", close=True)
            return None
        if length > MAX_BODY_BYTES:
            self.error_json(413, "request body too large", close=True)
            return None

        try:
            raw = self.rfile.read(length)
        except (OSError, ValueError):
            self.error_json(400, "could not read request body", close=True)
            return None
        if len(raw) != length:
            self.error_json(400, "incomplete request body", close=True)
            return None
        try:
            return raw.decode("utf-8")
        except UnicodeDecodeError:
            self.error_json(400, "request body must be valid UTF-8", close=True)
            return None

    def do_GET(self):
        target = self.parsed_target()
        if target is None:
            self.error_json(400, "invalid request target")
            return
        path = target.path

        if path == "/health":
            if target.query:
                self.error_json(404, "not found")
            else:
                self.send_json(200, {"status": "ok", "schema_version": SCHEMA_VERSION})
            return

        if path.startswith("/api/") or path == "/api":
            if not self.require_admin():
                return
            try:
                self.handle_admin_get(path, target.query)
            except Exception:
                self.error_json(500, "internal server error")
            return

        self.error_json(404, "not found")

    def handle_admin_get(self, path, query):
        if path == "/api/inboxes":
            if query:
                self.error_json(404, "not found")
                return
            connection = connect_db()
            try:
                rows = connection.execute(
                    "SELECT id, name, created_at FROM inboxes ORDER BY created_at DESC, id DESC"
                ).fetchall()
            finally:
                connection.close()
            self.send_json(200, {"inboxes": [dict(row) for row in rows]})
            return

        parts = path.split("/")
        if len(parts) == 5 and parts[1] == "api" and parts[2] == "inboxes" and parts[4] == "events":
            inbox_id = unquote(parts[3])
            if not inbox_id or "/" in inbox_id:
                self.error_json(404, "not found")
                return
            try:
                parameters = parse_qs(query, keep_blank_values=True, strict_parsing=True)
            except ValueError:
                self.error_json(400, "invalid query parameters")
                return
            if any(key != "limit" for key in parameters):
                self.error_json(400, "invalid query parameters")
                return
            limit_values = parameters.get("limit")
            if limit_values is None:
                limit = 20
            else:
                if len(limit_values) != 1:
                    self.error_json(400, "limit must occur once")
                    return
                raw_limit = limit_values[0]
                if not raw_limit or not raw_limit.isascii() or not raw_limit.isdecimal():
                    self.error_json(400, "invalid limit")
                    return
                limit = int(raw_limit, 10)
                if not 1 <= limit <= 100:
                    self.error_json(400, "invalid limit")
                    return

            connection = connect_db()
            try:
                exists = connection.execute("SELECT 1 FROM inboxes WHERE id = ?", (inbox_id,)).fetchone()
                if exists is None:
                    self.error_json(404, "not found")
                    return
                rows = connection.execute(
                    "SELECT id, inbox_id, external_id, received_at, body_sha256 "
                    "FROM events WHERE inbox_id = ? "
                    "ORDER BY received_at DESC, id DESC LIMIT ?",
                    (inbox_id, limit),
                ).fetchall()
            finally:
                connection.close()
            self.send_json(200, {"events": [dict(row) for row in rows]})
            return

        if len(parts) == 4 and parts[1] == "api" and parts[2] == "events":
            if query:
                self.error_json(404, "not found")
                return
            event_id = unquote(parts[3])
            if not event_id or "/" in event_id:
                self.error_json(404, "not found")
                return
            connection = connect_db()
            try:
                row = connection.execute(
                    "SELECT id, inbox_id, external_id, received_at, body_sha256, body, headers_json "
                    "FROM events WHERE id = ?",
                    (event_id,),
                ).fetchone()
            finally:
                connection.close()
            if row is None:
                self.error_json(404, "not found")
                return
            result = {
                "id": row["id"],
                "inbox_id": row["inbox_id"],
                "external_id": row["external_id"],
                "received_at": row["received_at"],
                "body_sha256": row["body_sha256"],
                "body": row["body"],
                "headers": json.loads(row["headers_json"]),
            }
            self.send_json(200, result)
            return

        self.error_json(404, "not found")

    def do_POST(self):
        target = self.parsed_target()
        if target is None:
            self.error_json(400, "invalid request target", close=True)
            return
        path = target.path

        if path.startswith("/api/") or path == "/api":
            if not self.require_admin():
                return
            if path != "/api/inboxes" or target.query:
                self.error_json(404, "not found")
                return
            body = self.read_post_body()
            if body is None:
                return
            try:
                self.create_inbox(body)
            except Exception:
                self.error_json(500, "internal server error")
            return

        parts = path.split("/")
        if len(parts) == 4 and parts[1] == "hooks" and not target.query:
            inbox_id = unquote(parts[2])
            token = unquote(parts[3])
            if not inbox_id or not token or "/" in inbox_id or "/" in token:
                self.error_json(404, "not found")
                return
            body = self.read_post_body()
            if body is None:
                return
            try:
                self.receive_hook(inbox_id, token, body)
            except Exception:
                self.error_json(500, "internal server error")
            return

        self.error_json(404, "not found")

    def create_inbox(self, body):
        try:
            value = json.loads(body)
        except (json.JSONDecodeError, ValueError):
            self.error_json(400, "invalid JSON")
            return
        if not isinstance(value, dict):
            self.error_json(400, "JSON body must be an object")
            return
        name = value.get("name")
        if not isinstance(name, str):
            self.error_json(400, "invalid name")
            return
        name = name.strip()
        if not 1 <= len(name) <= 80:
            self.error_json(400, "invalid name")
            return

        inbox_id = new_id()
        token = secrets.token_urlsafe(32)
        token_digest = hashlib.sha256(token.encode("utf-8")).hexdigest()
        created_at = utc_now()
        connection = connect_db()
        try:
            connection.execute(
                "INSERT INTO inboxes (id, name, token_sha256, created_at) VALUES (?, ?, ?, ?)",
                (inbox_id, name, token_digest, created_at),
            )
            connection.commit()
        finally:
            connection.close()
        self.send_json(201, {"id": inbox_id, "name": name, "created_at": created_at, "token": token})

    def receive_hook(self, inbox_id, token, body):
        supplied_digest = hashlib.sha256(token.encode("utf-8")).hexdigest()
        body_digest = hashlib.sha256(body.encode("utf-8")).hexdigest()
        connection = connect_db()
        try:
            inbox = connection.execute(
                "SELECT token_sha256 FROM inboxes WHERE id = ?", (inbox_id,)
            ).fetchone()
            if inbox is None or not hmac.compare_digest(inbox["token_sha256"], supplied_digest):
                self.error_json(404, "not found")
                return

            captured_headers = {}
            for header_name in ("content-type", "x-webhook-id", "x-event-type"):
                values = self.headers.get_all(header_name, [])
                if values:
                    captured_headers[header_name] = ", ".join(values)

            webhook_values = self.headers.get_all("X-Webhook-Id", [])
            external_id = None
            if webhook_values:
                candidate = ", ".join(webhook_values)
                if candidate:
                    external_id = candidate

            event_id = new_id()
            received_at = utc_now()
            headers_json = json.dumps(captured_headers, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
            try:
                connection.execute(
                    "INSERT INTO events "
                    "(id, inbox_id, external_id, received_at, headers_json, body, body_sha256) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (event_id, inbox_id, external_id, received_at, headers_json, body, body_digest),
                )
                connection.commit()
                self.send_json(201, {"id": event_id, "duplicate": False})
                return
            except sqlite3.IntegrityError:
                connection.rollback()
                if external_id is None:
                    raise
                existing = connection.execute(
                    "SELECT id, body_sha256, body FROM events WHERE inbox_id = ? AND external_id = ?",
                    (inbox_id, external_id),
                ).fetchone()
                if existing is None:
                    raise
                if existing["body_sha256"] == body_digest and existing["body"] == body:
                    self.send_json(200, {"id": existing["id"], "duplicate": True})
                else:
                    self.error_json(409, "idempotency key already used with a different body")
                return
        finally:
            connection.close()

    def method_not_allowed(self):
        self.error_json(405, "method not allowed")

    do_PUT = method_not_allowed
    do_DELETE = method_not_allowed
    do_PATCH = method_not_allowed
    do_OPTIONS = method_not_allowed
    do_HEAD = method_not_allowed
    do_CONNECT = method_not_allowed
    do_TRACE = method_not_allowed


class PhoenixServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True


if __name__ == "__main__":
    server = PhoenixServer(("127.0.0.1", PORT), PhoenixHandler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
