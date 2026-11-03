import os
import sys
import json
import re
import hmac
import hashlib
import secrets
import sqlite3
import uuid
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit, parse_qsl

MAX_BODY_BYTES = 65536
SCHEMA_VERSION = 1
DB_PATH = os.environ.get("PHOENIX_DB_PATH", "phoenix.sqlite")
ADMIN_TOKEN = os.environ.get("PHOENIX_ADMIN_TOKEN")

if not ADMIN_TOKEN:
    sys.stderr.write("PHOENIX_ADMIN_TOKEN is required\n")
    raise SystemExit(1)

try:
    PORT = int(os.environ.get("PORT", "8787"))
    if not 0 <= PORT <= 65535:
        raise ValueError
except ValueError:
    sys.stderr.write("PORT must be a valid integer port\n")
    raise SystemExit(1)


def utc_now():
    return datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def new_id():
    return uuid.uuid4().hex


def connect_db():
    connection = sqlite3.connect(DB_PATH, timeout=5.0)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys=ON")
    connection.execute("PRAGMA busy_timeout=5000")
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
except Exception:
    sys.stderr.write("Unable to initialize the database\n")
    raise SystemExit(1)


class RequestError(Exception):
    def __init__(self, status, message, close=False):
        super().__init__(message)
        self.status = status
        self.message = message
        self.close = close


class PhoenixHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "Phoenix"
    sys_version = ""

    def log_message(self, format, *args):
        return

    def _send_json(self, status, value, close=False, suppress_body=False):
        payload = json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        if close:
            self.send_header("Connection", "close")
            self.close_connection = True
        self.end_headers()
        if not suppress_body:
            self.wfile.write(payload)

    def _error(self, status, message, close=False, suppress_body=False):
        self._send_json(status, {"error": message}, close=close, suppress_body=suppress_body)

    def _is_authorized(self):
        values = self.headers.get_all("Authorization")
        if values is None or len(values) != 1:
            return False
        expected = "Bearer " + ADMIN_TOKEN
        return hmac.compare_digest(values[0], expected)

    def _require_admin(self):
        if not self._is_authorized():
            raise RequestError(401, "unauthorized")

    def _check_transfer_encoding(self):
        if self.headers.get_all("Transfer-Encoding") is not None:
            raise RequestError(400, "transfer encoding is not supported", close=True)

    def _read_body(self):
        self._check_transfer_encoding()
        values = self.headers.get_all("Content-Length")
        if values is None:
            length = 0
        elif len(values) != 1:
            raise RequestError(400, "malformed Content-Length", close=True)
        else:
            raw = values[0]
            if raw.startswith("-") and raw[1:].isdigit():
                raise RequestError(400, "negative Content-Length", close=True)
            if not re.fullmatch(r"[0-9]+", raw):
                raise RequestError(400, "malformed Content-Length", close=True)
            try:
                length = int(raw, 10)
            except ValueError:
                raise RequestError(400, "malformed Content-Length", close=True)
        if length > MAX_BODY_BYTES:
            raise RequestError(413, "request body too large", close=True)
        data = self.rfile.read(length)
        if len(data) != length:
            raise RequestError(400, "incomplete request body", close=True)
        try:
            return data.decode("utf-8")
        except UnicodeDecodeError:
            raise RequestError(400, "request body is not valid UTF-8", close=True)

    def _json_object_body(self):
        text = self._read_body()
        try:
            value = json.loads(
                text,
                parse_constant=lambda value: (_ for _ in ()).throw(ValueError("invalid constant")),
            )
        except (json.JSONDecodeError, ValueError):
            raise RequestError(400, "invalid JSON")
        if not isinstance(value, dict):
            raise RequestError(400, "JSON body must be an object")
        return value

    def _path_and_query(self):
        try:
            parsed = urlsplit(self.path)
        except ValueError:
            raise RequestError(400, "invalid request target")
        return parsed.path, parsed.query

    def _dispatch(self, method):
        self._check_transfer_encoding()
        path, query = self._path_and_query()

        if path.startswith("/api/") or path == "/api":
            self._require_admin()

        if method == "GET" and path == "/health":
            self._send_json(200, {"status": "ok", "schema_version": SCHEMA_VERSION})
            return

        if path == "/api/inboxes":
            if method == "POST":
                self._create_inbox()
            elif method == "GET":
                self._list_inboxes()
            else:
                raise RequestError(405, "method not allowed")
            return

        hook_match = re.fullmatch(r"/hooks/([^/]+)/([^/]+)", path)
        if hook_match:
            if method != "POST":
                raise RequestError(405, "method not allowed")
            self._receive_hook(hook_match.group(1), hook_match.group(2))
            return

        inbox_events_match = re.fullmatch(r"/api/inboxes/([^/]+)/events", path)
        if inbox_events_match:
            if method != "GET":
                raise RequestError(405, "method not allowed")
            self._list_events(inbox_events_match.group(1), query)
            return

        event_match = re.fullmatch(r"/api/events/([^/]+)", path)
        if event_match:
            if method != "GET":
                raise RequestError(405, "method not allowed")
            self._get_event(event_match.group(1))
            return

        if path == "/health":
            raise RequestError(405, "method not allowed")
        raise RequestError(404, "not found")

    def _handle(self, method):
        try:
            self._dispatch(method)
        except RequestError as exc:
            self._error(exc.status, exc.message, close=exc.close, suppress_body=(method == "HEAD"))
        except (BrokenPipeError, ConnectionResetError):
            self.close_connection = True
        except Exception:
            self._error(500, "internal server error", suppress_body=(method == "HEAD"))

    def do_GET(self):
        self._handle("GET")

    def do_POST(self):
        self._handle("POST")

    def do_PUT(self):
        self._handle("PUT")

    def do_PATCH(self):
        self._handle("PATCH")

    def do_DELETE(self):
        self._handle("DELETE")

    def do_HEAD(self):
        self._handle("HEAD")

    def do_OPTIONS(self):
        self._handle("OPTIONS")

    def do_CONNECT(self):
        self._handle("CONNECT")

    def do_TRACE(self):
        self._handle("TRACE")

    def _create_inbox(self):
        value = self._json_object_body()
        name = value.get("name")
        if not isinstance(name, str):
            raise RequestError(400, "invalid name")
        name = name.strip()
        if not 1 <= len(name) <= 80:
            raise RequestError(400, "invalid name")

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
        supplied_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
        body_hash = hashlib.sha256(body.encode("utf-8")).hexdigest()

        captured_headers = {}
        for name in ("content-type", "x-webhook-id", "x-event-type"):
            value = self.headers.get(name)
            if value is not None:
                captured_headers[name] = value
        external_id = self.headers.get("x-webhook-id")
        if external_id == "" or external_id is None:
            external_id = None

        connection = connect_db()
        try:
            row = connection.execute(
                "SELECT token_sha256 FROM inboxes WHERE id = ?", (inbox_id,)
            ).fetchone()
            if row is None or not hmac.compare_digest(row["token_sha256"], supplied_hash):
                raise RequestError(404, "not found")

            connection.execute("BEGIN IMMEDIATE")
            if external_id is not None:
                existing = connection.execute(
                    "SELECT id, body FROM events WHERE inbox_id = ? AND external_id = ?",
                    (inbox_id, external_id),
                ).fetchone()
                if existing is not None:
                    connection.rollback()
                    if existing["body"] == body:
                        self._send_json(200, {"id": existing["id"], "duplicate": True})
                        return
                    raise RequestError(409, "idempotency key conflict")

            event_id = new_id()
            received_at = utc_now()
            connection.execute(
                """INSERT INTO events
                   (id, inbox_id, external_id, received_at, headers_json, body, body_sha256)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    event_id,
                    inbox_id,
                    external_id,
                    received_at,
                    json.dumps(captured_headers, ensure_ascii=False, separators=(",", ":")),
                    body,
                    body_hash,
                ),
            )
            connection.commit()
        except RequestError:
            if connection.in_transaction:
                connection.rollback()
            raise
        except Exception:
            if connection.in_transaction:
                connection.rollback()
            raise
        finally:
            connection.close()
        self._send_json(201, {"id": event_id, "duplicate": False})

    def _list_events(self, inbox_id, query):
        try:
            parameters = parse_qsl(query, keep_blank_values=True, strict_parsing=False)
        except ValueError:
            raise RequestError(400, "invalid query")
        limit_values = [value for key, value in parameters if key == "limit"]
        if len(limit_values) > 1:
            raise RequestError(400, "invalid limit")
        if limit_values:
            raw_limit = limit_values[0]
            if not re.fullmatch(r"[0-9]+", raw_limit):
                raise RequestError(400, "invalid limit")
            limit = int(raw_limit)
            if not 1 <= limit <= 100:
                raise RequestError(400, "invalid limit")
        else:
            limit = 20

        connection = connect_db()
        try:
            inbox = connection.execute("SELECT 1 FROM inboxes WHERE id = ?", (inbox_id,)).fetchone()
            if inbox is None:
                raise RequestError(404, "not found")
            rows = connection.execute(
                """SELECT id, inbox_id, external_id, received_at, body_sha256
                   FROM events WHERE inbox_id = ?
                   ORDER BY received_at DESC, id DESC LIMIT ?""",
                (inbox_id, limit),
            ).fetchall()
        finally:
            connection.close()
        self._send_json(200, {"events": [dict(row) for row in rows]})

    def _get_event(self, event_id):
        connection = connect_db()
        try:
            row = connection.execute(
                """SELECT id, inbox_id, external_id, received_at, body_sha256,
                          body, headers_json
                   FROM events WHERE id = ?""",
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
        self._send_json(200, result)


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
