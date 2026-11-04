import os
import re
import json
import hmac
import uuid
import base64
import hashlib
import secrets
import sqlite3
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit, parse_qsl, unquote

HOST = "127.0.0.1"
MAX_BODY = 65536
SCHEMA_VERSION = 1

ADMIN_TOKEN = os.environ.get("PHOENIX_ADMIN_TOKEN", "")
if not ADMIN_TOKEN:
    raise SystemExit("PHOENIX_ADMIN_TOKEN is required")

try:
    PORT = int(os.environ.get("PORT", "8787"))
    if not 1 <= PORT <= 65535:
        raise ValueError
except ValueError:
    raise SystemExit("PORT must be an integer between 1 and 65535")

DB_PATH = os.environ.get("PHOENIX_DB_PATH", "phoenix.sqlite")


def now_utc():
    return datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def new_id():
    return uuid.uuid4().hex


def connect_db():
    connection = sqlite3.connect(DB_PATH, timeout=10.0)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys=ON")
    connection.execute("PRAGMA busy_timeout=10000")
    return connection


def initialize_db():
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


initialize_db()


class RequestError(Exception):
    def __init__(self, status, message, close=False):
        super().__init__(message)
        self.status = status
        self.message = message
        self.close = close


class PhoenixHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "Phoenix/1.0"
    sys_version = ""

    def log_message(self, format, *args):
        return

    def send_json(self, status, payload):
        encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(encoded)))
        if self.close_connection:
            self.send_header("Connection", "close")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(encoded)

    def fail(self, status, message, close=False):
        if close:
            self.close_connection = True
        self.send_json(status, {"error": message})

    def admin_authorized(self):
        values = self.headers.get_all("Authorization", [])
        if len(values) != 1:
            return False
        supplied = values[0]
        prefix = "Bearer "
        if not supplied.startswith(prefix):
            return False
        token = supplied[len(prefix):]
        if not token or " " in token or "\t" in token:
            return False
        return hmac.compare_digest(token.encode("utf-8"), ADMIN_TOKEN.encode("utf-8"))

    def read_body(self):
        if self.headers.get_all("Transfer-Encoding", []):
            raise RequestError(400, "transfer encoding is not supported", True)

        lengths = self.headers.get_all("Content-Length", [])
        if not lengths:
            if self.command == "POST":
                return b""
            return b""
        if len(lengths) != 1:
            raise RequestError(400, "malformed Content-Length", True)

        value = lengths[0].strip()
        if value.startswith("-"):
            raise RequestError(400, "negative Content-Length", True)
        if not re.fullmatch(r"[0-9]+", value):
            raise RequestError(400, "malformed Content-Length", True)

        length = int(value)
        if length > MAX_BODY:
            raise RequestError(413, "request body too large", True)

        data = self.rfile.read(length)
        if len(data) != length:
            raise RequestError(400, "incomplete request body", True)
        return data

    def decoded_path_parts(self, path):
        try:
            return [unquote(part, errors="strict") for part in path.split("/") if part]
        except (UnicodeDecodeError, ValueError):
            raise RequestError(404, "not found")

    def dispatch(self):
        parsed = urlsplit(self.path)
        path = parsed.path

        if self.headers.get_all("Transfer-Encoding", []):
            raise RequestError(400, "transfer encoding is not supported", True)

        if path.startswith("/api/") or path == "/api":
            if not self.admin_authorized():
                raise RequestError(401, "unauthorized")

        parts = self.decoded_path_parts(path)

        if self.command == "GET" and path == "/health":
            self.send_json(200, {"status": "ok", "schema_version": SCHEMA_VERSION})
            return

        if parts == ["api", "inboxes"]:
            if self.command == "POST":
                self.create_inbox()
            elif self.command == "GET":
                self.list_inboxes()
            else:
                raise RequestError(405, "method not allowed")
            return

        if len(parts) == 4 and parts[0] == "api" and parts[1] == "inboxes" and parts[3] == "events":
            if self.command != "GET":
                raise RequestError(405, "method not allowed")
            self.list_events(parts[2], parsed.query)
            return

        if len(parts) == 3 and parts[0] == "api" and parts[1] == "events":
            if self.command != "GET":
                raise RequestError(405, "method not allowed")
            self.get_event(parts[2])
            return

        if len(parts) == 3 and parts[0] == "hooks":
            if self.command != "POST":
                raise RequestError(405, "method not allowed")
            self.receive_hook(parts[1], parts[2])
            return

        if path == "/health" or path.startswith("/health/"):
            if self.command != "GET":
                raise RequestError(405, "method not allowed")

        raise RequestError(404, "not found")

    def create_inbox(self):
        raw = self.read_body()
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            raise RequestError(400, "request body is not valid UTF-8")
        try:
            document = json.loads(text)
        except (json.JSONDecodeError, ValueError):
            raise RequestError(400, "invalid JSON")
        if not isinstance(document, dict):
            raise RequestError(400, "JSON body must be an object")
        name = document.get("name")
        if not isinstance(name, str):
            raise RequestError(400, "invalid name")
        name = name.strip()
        if not 1 <= len(name) <= 80:
            raise RequestError(400, "invalid name")

        inbox_id = new_id()
        token = base64.urlsafe_b64encode(secrets.token_bytes(32)).rstrip(b"=").decode("ascii")
        token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
        created_at = now_utc()
        connection = connect_db()
        try:
            connection.execute(
                "INSERT INTO inboxes (id, name, token_sha256, created_at) VALUES (?, ?, ?, ?)",
                (inbox_id, name, token_hash, created_at),
            )
            connection.commit()
        finally:
            connection.close()
        self.send_json(201, {"id": inbox_id, "name": name, "created_at": created_at, "token": token})

    def list_inboxes(self):
        connection = connect_db()
        try:
            rows = connection.execute(
                "SELECT id, name, created_at FROM inboxes ORDER BY created_at DESC, id DESC"
            ).fetchall()
        finally:
            connection.close()
        self.send_json(200, {"inboxes": [dict(row) for row in rows]})

    def receive_hook(self, inbox_id, token):
        raw = self.read_body()
        try:
            body = raw.decode("utf-8")
        except UnicodeDecodeError:
            raise RequestError(400, "request body is not valid UTF-8")

        supplied_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
        connection = connect_db()
        try:
            inbox = connection.execute(
                "SELECT token_sha256 FROM inboxes WHERE id = ?", (inbox_id,)
            ).fetchone()
            expected = inbox["token_sha256"] if inbox is not None else "0" * 64
            matches = hmac.compare_digest(supplied_hash, expected)
            if inbox is None or not matches:
                raise RequestError(404, "not found")

            external_header = self.headers.get("X-Webhook-Id")
            external_id = external_header if external_header is not None and external_header != "" else None
            captured = {}
            for header_name in ("content-type", "x-webhook-id", "x-event-type"):
                value = self.headers.get(header_name)
                if value is not None:
                    captured[header_name] = value

            body_hash = hashlib.sha256(raw).hexdigest()
            event_id = new_id()
            received_at = now_utc()
            headers_json = json.dumps(captured, ensure_ascii=False, separators=(",", ":"))

            try:
                connection.execute("BEGIN IMMEDIATE")
                if external_id is not None:
                    existing = connection.execute(
                        "SELECT id, body_sha256 FROM events WHERE inbox_id = ? AND external_id = ?",
                        (inbox_id, external_id),
                    ).fetchone()
                    if existing is not None:
                        connection.rollback()
                        if hmac.compare_digest(existing["body_sha256"], body_hash):
                            self.send_json(200, {"id": existing["id"], "duplicate": True})
                            return
                        raise RequestError(409, "idempotency key already used with a different body")

                connection.execute(
                    "INSERT INTO events (id, inbox_id, external_id, received_at, headers_json, body, body_sha256) VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (event_id, inbox_id, external_id, received_at, headers_json, body, body_hash),
                )
                connection.commit()
            except sqlite3.IntegrityError:
                connection.rollback()
                if external_id is None:
                    raise
                existing = connection.execute(
                    "SELECT id, body_sha256 FROM events WHERE inbox_id = ? AND external_id = ?",
                    (inbox_id, external_id),
                ).fetchone()
                if existing is not None and hmac.compare_digest(existing["body_sha256"], body_hash):
                    self.send_json(200, {"id": existing["id"], "duplicate": True})
                    return
                raise RequestError(409, "idempotency key already used with a different body")
        finally:
            connection.close()

        self.send_json(201, {"id": event_id, "duplicate": False})

    def list_events(self, inbox_id, query):
        limit_values = [value for key, value in parse_qsl(query, keep_blank_values=True) if key == "limit"]
        if len(limit_values) > 1:
            raise RequestError(400, "invalid limit")
        limit = 20
        if limit_values:
            value = limit_values[0]
            if not re.fullmatch(r"[0-9]+", value):
                raise RequestError(400, "invalid limit")
            limit = int(value)
            if not 1 <= limit <= 100:
                raise RequestError(400, "invalid limit")

        connection = connect_db()
        try:
            inbox = connection.execute("SELECT 1 FROM inboxes WHERE id = ?", (inbox_id,)).fetchone()
            if inbox is None:
                raise RequestError(404, "not found")
            rows = connection.execute(
                "SELECT id, inbox_id, external_id, received_at, body_sha256 FROM events WHERE inbox_id = ? ORDER BY received_at DESC, id DESC LIMIT ?",
                (inbox_id, limit),
            ).fetchall()
        finally:
            connection.close()
        self.send_json(200, {"events": [dict(row) for row in rows]})

    def get_event(self, event_id):
        connection = connect_db()
        try:
            row = connection.execute(
                "SELECT id, inbox_id, external_id, received_at, body_sha256, body, headers_json FROM events WHERE id = ?",
                (event_id,),
            ).fetchone()
        finally:
            connection.close()
        if row is None:
            raise RequestError(404, "not found")
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

    def handle_request(self):
        try:
            self.dispatch()
        except RequestError as error:
            self.fail(error.status, error.message, error.close)
        except (BrokenPipeError, ConnectionResetError):
            self.close_connection = True
        except Exception:
            try:
                self.fail(500, "internal server error", True)
            except (BrokenPipeError, ConnectionResetError):
                self.close_connection = True

    do_GET = handle_request
    do_POST = handle_request
    do_PUT = handle_request
    do_PATCH = handle_request
    do_DELETE = handle_request
    do_HEAD = handle_request
    do_OPTIONS = handle_request
    do_TRACE = handle_request
    do_CONNECT = handle_request


if __name__ == "__main__":
    server = ThreadingHTTPServer((HOST, PORT), PhoenixHandler)
    server.daemon_threads = True
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
