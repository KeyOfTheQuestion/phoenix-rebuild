import os
import sys
import json
import hmac
import hashlib
import secrets
import sqlite3
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit, parse_qs

MAX_BODY_BYTES = 65536
SCHEMA_VERSION = 1

ADMIN_TOKEN = os.environ.get("PHOENIX_ADMIN_TOKEN", "")
if not ADMIN_TOKEN:
    raise SystemExit("PHOENIX_ADMIN_TOKEN is required and must not be empty")

DB_PATH = os.environ.get("PHOENIX_DB_PATH", "phoenix.sqlite")
try:
    PORT = int(os.environ.get("PORT", "8787"))
except ValueError:
    raise SystemExit("PORT must be an integer")
if not 1 <= PORT <= 65535:
    raise SystemExit("PORT must be between 1 and 65535")


def utc_now():
    return datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def new_id():
    return secrets.token_urlsafe(24)


def connect_db():
    connection = sqlite3.connect(DB_PATH, timeout=10.0)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys=ON")
    connection.execute("PRAGMA busy_timeout=10000")
    return connection


def initialize_database():
    connection = connect_db()
    try:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS inboxes (
              id TEXT PRIMARY KEY,
              name TEXT NOT NULL,
              token_sha256 TEXT NOT NULL,
              created_at TEXT NOT NULL
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS events (
              id TEXT PRIMARY KEY,
              inbox_id TEXT NOT NULL REFERENCES inboxes(id),
              external_id TEXT,
              received_at TEXT NOT NULL,
              headers_json TEXT NOT NULL,
              body TEXT NOT NULL,
              body_sha256 TEXT NOT NULL,
              UNIQUE(inbox_id, external_id)
            )
            """
        )
        connection.commit()
    finally:
        connection.close()


class PhoenixServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "Phoenix/1.0"
    sys_version = ""

    def log_message(self, format, *args):
        return

    def _send_json(self, status, payload, close=False):
        data = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        if close:
            self.send_header("Connection", "close")
            self.close_connection = True
        self.end_headers()
        if self.command != "HEAD":
            try:
                self.wfile.write(data)
            except (BrokenPipeError, ConnectionResetError):
                pass

    def _error(self, status, message, close=False):
        self._send_json(status, {"error": message}, close=close)

    def _admin_authorized(self):
        values = self.headers.get_all("Authorization", [])
        if len(values) != 1:
            return False
        supplied = values[0]
        prefix = "Bearer "
        if not supplied.startswith(prefix):
            return False
        return hmac.compare_digest(supplied[len(prefix):], ADMIN_TOKEN)

    def _read_body(self):
        if self.headers.get_all("Transfer-Encoding", []):
            self._error(400, "Transfer-Encoding is not supported", close=True)
            return None

        lengths = self.headers.get_all("Content-Length", [])
        if not lengths:
            length = 0
        elif len(lengths) != 1:
            self._error(400, "malformed Content-Length", close=True)
            return None
        else:
            raw_length = lengths[0]
            if raw_length.startswith("-"):
                self._error(400, "negative Content-Length", close=True)
                return None
            if not raw_length or not raw_length.isdecimal():
                self._error(400, "malformed Content-Length", close=True)
                return None
            try:
                length = int(raw_length, 10)
            except ValueError:
                self._error(400, "malformed Content-Length", close=True)
                return None

        if length > MAX_BODY_BYTES:
            self._error(413, "request body too large", close=True)
            return None

        try:
            body = self.rfile.read(length)
        except (OSError, ValueError):
            self._error(400, "could not read request body", close=True)
            return None
        if len(body) != length:
            self._error(400, "incomplete request body", close=True)
            return None
        try:
            return body.decode("utf-8", errors="strict")
        except UnicodeDecodeError:
            self._error(400, "request body must be valid UTF-8", close=True)
            return None

    def _captured_headers(self):
        captured = {}
        for name in ("content-type", "x-webhook-id", "x-event-type"):
            values = self.headers.get_all(name, [])
            if values:
                captured[name] = ", ".join(values)
        return captured

    def _route_kind(self, path):
        if path == "/health":
            return "health"
        if path == "/api/inboxes":
            return "inboxes"
        if path.startswith("/api/inboxes/") and path.endswith("/events"):
            parts = path.split("/")
            if len(parts) == 5 and parts[3]:
                return "inbox_events"
        if path.startswith("/api/events/"):
            parts = path.split("/")
            if len(parts) == 4 and parts[3]:
                return "event"
        if path.startswith("/hooks/"):
            parts = path.split("/")
            if len(parts) == 4 and parts[2] and parts[3]:
                return "hook"
        return None

    def _dispatch(self):
        try:
            parsed = urlsplit(self.path)
            path = parsed.path
        except Exception:
            self._error(400, "malformed request target")
            return

        if path == "/api" or path.startswith("/api/"):
            if not self._admin_authorized():
                self._error(401, "unauthorized")
                return

        kind = self._route_kind(path)
        allowed = {
            "health": {"GET"},
            "inboxes": {"GET", "POST"},
            "inbox_events": {"GET"},
            "event": {"GET"},
            "hook": {"POST"},
        }

        if kind is None:
            self._error(404, "not found")
            return
        if self.command not in allowed[kind]:
            self._error(405, "method not allowed")
            return

        if kind == "health":
            self._send_json(200, {"status": "ok", "schema_version": SCHEMA_VERSION})
        elif kind == "inboxes" and self.command == "POST":
            self._create_inbox()
        elif kind == "inboxes":
            self._list_inboxes()
        elif kind == "hook":
            parts = path.split("/")
            self._receive_hook(parts[2], parts[3])
        elif kind == "inbox_events":
            parts = path.split("/")
            self._list_events(parts[3], parsed.query)
        elif kind == "event":
            parts = path.split("/")
            self._get_event(parts[3])

    def _create_inbox(self):
        body = self._read_body()
        if body is None:
            return
        try:
            value = json.loads(body)
        except (json.JSONDecodeError, ValueError):
            self._error(400, "invalid JSON")
            return
        if not isinstance(value, dict):
            self._error(400, "request must be a JSON object")
            return
        name = value.get("name")
        if not isinstance(name, str):
            self._error(400, "name must be a string")
            return
        name = name.strip()
        if not 1 <= len(name) <= 80:
            self._error(400, "name must contain between 1 and 80 characters")
            return

        inbox_id = new_id()
        token = secrets.token_urlsafe(32)
        token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
        created_at = utc_now()
        connection = connect_db()
        try:
            connection.execute(
                "INSERT INTO inboxes (id, name, token_sha256, created_at) VALUES (?, ?, ?, ?)",
                (inbox_id, name, token_hash, created_at),
            )
            connection.commit()
        finally:
            connection.close()
        self._send_json(201, {"id": inbox_id, "name": name, "created_at": created_at, "token": token})

    def _list_inboxes(self):
        connection = connect_db()
        try:
            rows = connection.execute(
                "SELECT id, name, created_at FROM inboxes ORDER BY created_at DESC, id DESC"
            ).fetchall()
        finally:
            connection.close()
        self._send_json(200, {"inboxes": [dict(row) for row in rows]})

    def _receive_hook(self, inbox_id, token):
        body = self._read_body()
        if body is None:
            return

        supplied_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
        headers = self._captured_headers()
        external_id = headers.get("x-webhook-id")
        if external_id == "":
            external_id = None
        body_hash = hashlib.sha256(body.encode("utf-8")).hexdigest()
        event_id = new_id()
        received_at = utc_now()
        headers_json = json.dumps(headers, ensure_ascii=False, separators=(",", ":"))

        connection = connect_db()
        try:
            connection.execute("BEGIN IMMEDIATE")
            inbox = connection.execute(
                "SELECT token_sha256 FROM inboxes WHERE id = ?", (inbox_id,)
            ).fetchone()
            if inbox is None or not hmac.compare_digest(inbox["token_sha256"], supplied_hash):
                connection.rollback()
                self._error(404, "not found")
                return

            if external_id is not None:
                existing = connection.execute(
                    "SELECT id, body_sha256 FROM events WHERE inbox_id = ? AND external_id = ?",
                    (inbox_id, external_id),
                ).fetchone()
                if existing is not None:
                    connection.rollback()
                    if hmac.compare_digest(existing["body_sha256"], body_hash):
                        self._send_json(200, {"id": existing["id"], "duplicate": True})
                    else:
                        self._error(409, "idempotency key already used with a different body")
                    return

            try:
                connection.execute(
                    """
                    INSERT INTO events
                    (id, inbox_id, external_id, received_at, headers_json, body, body_sha256)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
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
                    self._send_json(200, {"id": existing["id"], "duplicate": True})
                else:
                    self._error(409, "idempotency key already used with a different body")
                return
        finally:
            connection.close()
        self._send_json(201, {"id": event_id, "duplicate": False})

    def _list_events(self, inbox_id, query):
        try:
            parameters = parse_qs(query, keep_blank_values=True, strict_parsing=True)
        except ValueError:
            self._error(400, "invalid query string")
            return
        if any(key != "limit" for key in parameters):
            self._error(400, "invalid query parameter")
            return
        values = parameters.get("limit")
        if values is None:
            limit = 20
        else:
            if len(values) != 1 or not values[0].isdecimal():
                self._error(400, "limit must be a decimal integer between 1 and 100")
                return
            limit = int(values[0], 10)
            if not 1 <= limit <= 100:
                self._error(400, "limit must be a decimal integer between 1 and 100")
                return

        connection = connect_db()
        try:
            inbox = connection.execute("SELECT 1 FROM inboxes WHERE id = ?", (inbox_id,)).fetchone()
            if inbox is None:
                self._error(404, "not found")
                return
            rows = connection.execute(
                """
                SELECT id, inbox_id, external_id, received_at, body_sha256
                FROM events
                WHERE inbox_id = ?
                ORDER BY received_at DESC, id DESC
                LIMIT ?
                """,
                (inbox_id, limit),
            ).fetchall()
        finally:
            connection.close()
        self._send_json(200, {"events": [dict(row) for row in rows]})

    def _get_event(self, event_id):
        connection = connect_db()
        try:
            row = connection.execute(
                """
                SELECT id, inbox_id, external_id, received_at, body_sha256,
                       body, headers_json
                FROM events WHERE id = ?
                """,
                (event_id,),
            ).fetchone()
        finally:
            connection.close()
        if row is None:
            self._error(404, "not found")
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
        self._send_json(200, result)

    def _safe_dispatch(self):
        try:
            self._dispatch()
        except Exception:
            try:
                self._error(500, "internal server error", close=True)
            except Exception:
                self.close_connection = True

    do_GET = _safe_dispatch
    do_POST = _safe_dispatch
    do_PUT = _safe_dispatch
    do_PATCH = _safe_dispatch
    do_DELETE = _safe_dispatch
    do_HEAD = _safe_dispatch
    do_OPTIONS = _safe_dispatch
    do_TRACE = _safe_dispatch
    do_CONNECT = _safe_dispatch


if __name__ == "__main__":
    initialize_database()
    server = PhoenixServer(("127.0.0.1", PORT), Handler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
