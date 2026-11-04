import os
import sys
import json
import re
import hmac
import hashlib
import secrets
import sqlite3
from datetime import datetime, timezone, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit, parse_qsl

MAX_BODY = 65536
DEFAULT_PORT = 8787
DEFAULT_DB_PATH = "phoenix.sqlite"


def utc_now():
    return datetime.now(timezone.utc)


def iso_utc(value=None):
    if value is None:
        value = utc_now()
    return value.isoformat(timespec="microseconds").replace("+00:00", "Z")


def open_db(path):
    connection = sqlite3.connect(path, timeout=10.0, isolation_level=None)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys=ON")
    connection.execute("PRAGMA busy_timeout=10000")
    return connection


def migrate_database(path):
    connection = open_db(path)
    try:
        version = connection.execute("PRAGMA user_version").fetchone()[0]
        if version not in (0, 1, 2):
            raise RuntimeError("unsupported database schema version")
        if version == 2:
            return

        connection.execute("BEGIN IMMEDIATE")
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
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS retention_policies (
                  inbox_id TEXT PRIMARY KEY REFERENCES inboxes(id),
                  max_age_seconds INTEGER NOT NULL
                    CHECK(max_age_seconds BETWEEN 1 AND 2592000)
                )
                """
            )
            connection.execute("PRAGMA user_version=2")
            connection.execute("COMMIT")
        except Exception:
            connection.execute("ROLLBACK")
            raise
    finally:
        connection.close()


class PhoenixServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address, handler, db_path, admin_token):
        super().__init__(address, handler)
        self.db_path = db_path
        self.admin_token = admin_token


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "Phoenix"
    sys_version = ""

    def log_message(self, format, *args):
        return

    def do_GET(self):
        self.dispatch()

    def do_POST(self):
        self.dispatch()

    def do_PUT(self):
        self.dispatch()

    def do_DELETE(self):
        self.dispatch()

    def do_PATCH(self):
        self.dispatch()

    def do_HEAD(self):
        self.dispatch()

    def do_OPTIONS(self):
        self.dispatch()

    def do_CONNECT(self):
        self.dispatch()

    def do_TRACE(self):
        self.dispatch()

    def send_json(self, status, value, extra_headers=None):
        payload = json.dumps(
            value, ensure_ascii=False, separators=(",", ":")
        ).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        if extra_headers:
            for name, value in extra_headers.items():
                self.send_header(name, value)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(payload)

    def error(self, status, message, close=False, allow=None):
        if close:
            self.close_connection = True
        headers = {"Allow": allow} if allow else None
        self.send_json(status, {"error": message}, headers)

    def database(self):
        return open_db(self.server.db_path)

    def authenticated(self):
        values = self.headers.get_all("Authorization") or []
        if len(values) != 1:
            return False
        expected = "Bearer " + self.server.admin_token
        return hmac.compare_digest(values[0], expected)

    def reject_transfer_encoding(self):
        if self.headers.get_all("Transfer-Encoding"):
            self.error(400, "transfer encoding is not supported", close=True)
            return True
        return False

    def read_body(self, missing_as_empty=True):
        if self.reject_transfer_encoding():
            return None

        values = self.headers.get_all("Content-Length") or []
        if not values:
            if missing_as_empty:
                length = 0
            else:
                self.error(400, "missing Content-Length", close=True)
                return None
        elif len(values) != 1:
            self.error(400, "malformed Content-Length", close=True)
            return None
        else:
            raw = values[0].strip()
            if raw.startswith("-"):
                self.error(400, "negative Content-Length", close=True)
                return None
            if not re.fullmatch(r"[0-9]+", raw):
                self.error(400, "malformed Content-Length", close=True)
                return None
            try:
                length = int(raw, 10)
            except ValueError:
                self.error(400, "malformed Content-Length", close=True)
                return None

        if length > MAX_BODY:
            self.error(413, "request body too large", close=True)
            return None

        try:
            data = self.rfile.read(length)
        except Exception:
            self.error(400, "could not read request body", close=True)
            return None
        if len(data) != length:
            self.error(400, "incomplete request body", close=True)
            return None
        try:
            return data.decode("utf-8")
        except UnicodeDecodeError:
            self.error(400, "request body is not valid UTF-8", close=True)
            return None

    def read_json(self, allow_empty=False):
        text = self.read_body(missing_as_empty=self.command == "POST")
        if text is None:
            return None, False
        if text == "":
            if allow_empty:
                return {}, True
            self.error(400, "request body must contain JSON", close=True)
            return None, False
        try:
            return json.loads(text), True
        except (ValueError, TypeError):
            self.error(400, "malformed JSON", close=True)
            return None, False

    def dispatch(self):
        try:
            target = urlsplit(self.path)
            path = target.path

            if path.startswith("/api/") or path == "/api":
                if not self.authenticated():
                    self.error(401, "unauthorized")
                    return
                self.route_api(path, target.query)
                return

            if path == "/health":
                if self.command != "GET":
                    self.error(405, "method not allowed", allow="GET")
                    return
                if self.reject_transfer_encoding():
                    return
                self.send_json(200, {"status": "ok", "schema_version": 2})
                return

            match = re.fullmatch(r"/hooks/([^/]+)/([^/]+)", path)
            if match:
                if self.command != "POST":
                    self.error(405, "method not allowed", allow="POST")
                    return
                self.receive_hook(match.group(1), match.group(2))
                return

            self.error(404, "not found")
        except (BrokenPipeError, ConnectionResetError):
            self.close_connection = True
        except Exception:
            try:
                self.error(500, "internal server error", close=True)
            except Exception:
                self.close_connection = True

    def route_api(self, path, query):
        if path == "/api/inboxes":
            if self.command == "POST":
                self.create_inbox()
            elif self.command == "GET":
                self.list_inboxes()
            else:
                self.error(405, "method not allowed", allow="GET, POST")
            return

        match = re.fullmatch(r"/api/inboxes/([^/]+)/events", path)
        if match:
            if self.command != "GET":
                self.error(405, "method not allowed", allow="GET")
            else:
                self.list_events(match.group(1), query)
            return

        match = re.fullmatch(r"/api/events/([^/]+)", path)
        if match:
            if self.command != "GET":
                self.error(405, "method not allowed", allow="GET")
            else:
                self.get_event(match.group(1))
            return

        match = re.fullmatch(r"/api/inboxes/([^/]+)/retention", path)
        if match:
            if self.command == "GET":
                self.get_retention(match.group(1))
            elif self.command == "PUT":
                self.put_retention(match.group(1))
            else:
                self.error(405, "method not allowed", allow="GET, PUT")
            return

        match = re.fullmatch(r"/api/inboxes/([^/]+)/expire", path)
        if match:
            if self.command == "POST":
                self.expire_events(match.group(1))
            else:
                self.error(405, "method not allowed", allow="POST")
            return

        self.error(404, "not found")

    def create_inbox(self):
        value, ok = self.read_json()
        if not ok:
            return
        if not isinstance(value, dict):
            self.error(400, "JSON body must be an object", close=True)
            return
        name = value.get("name")
        if not isinstance(name, str):
            self.error(400, "invalid inbox name", close=True)
            return
        name = name.strip()
        if not 1 <= len(name) <= 80:
            self.error(400, "invalid inbox name", close=True)
            return

        inbox_id = secrets.token_urlsafe(24)
        token = secrets.token_urlsafe(32)
        token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
        created_at = iso_utc()
        connection = self.database()
        try:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                "INSERT INTO inboxes (id, name, token_sha256, created_at) VALUES (?, ?, ?, ?)",
                (inbox_id, name, token_hash, created_at),
            )
            connection.execute("COMMIT")
        except Exception:
            if connection.in_transaction:
                connection.execute("ROLLBACK")
            raise
        finally:
            connection.close()

        self.send_json(201, {
            "id": inbox_id,
            "name": name,
            "created_at": created_at,
            "token": token,
        })

    def list_inboxes(self):
        connection = self.database()
        try:
            rows = connection.execute(
                "SELECT id, name, created_at FROM inboxes ORDER BY created_at DESC, id DESC"
            ).fetchall()
        finally:
            connection.close()
        self.send_json(200, {"inboxes": [dict(row) for row in rows]})

    def receive_hook(self, inbox_id, token):
        supplied_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
        connection = self.database()
        try:
            row = connection.execute(
                "SELECT token_sha256 FROM inboxes WHERE id = ?", (inbox_id,)
            ).fetchone()
        finally:
            connection.close()
        if row is None or not hmac.compare_digest(row["token_sha256"], supplied_hash):
            self.error(404, "not found")
            return

        body = self.read_body(missing_as_empty=True)
        if body is None:
            return
        body_hash = hashlib.sha256(body.encode("utf-8")).hexdigest()

        captured = {}
        for header_name in ("content-type", "x-webhook-id", "x-event-type"):
            values = self.headers.get_all(header_name)
            if values:
                captured[header_name] = values[0]
        external_id = captured.get("x-webhook-id")
        if external_id == "" or external_id is None:
            external_id = None

        headers_json = json.dumps(captured, ensure_ascii=False, separators=(",", ":"))
        event_id = secrets.token_urlsafe(24)
        received_at = iso_utc()
        connection = self.database()
        try:
            connection.execute("BEGIN IMMEDIATE")
            if external_id is not None:
                existing = connection.execute(
                    "SELECT id, body_sha256 FROM events WHERE inbox_id = ? AND external_id = ?",
                    (inbox_id, external_id),
                ).fetchone()
                if existing is not None:
                    connection.execute("COMMIT")
                    if hmac.compare_digest(existing["body_sha256"], body_hash):
                        self.send_json(200, {"id": existing["id"], "duplicate": True})
                    else:
                        self.error(409, "idempotency key conflicts with an existing event")
                    return
            connection.execute(
                """
                INSERT INTO events
                  (id, inbox_id, external_id, received_at, headers_json, body, body_sha256)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (event_id, inbox_id, external_id, received_at, headers_json, body, body_hash),
            )
            connection.execute("COMMIT")
        except Exception:
            if connection.in_transaction:
                connection.execute("ROLLBACK")
            raise
        finally:
            connection.close()

        self.send_json(201, {"id": event_id, "duplicate": False})

    def list_events(self, inbox_id, query):
        pairs = parse_qsl(query, keep_blank_values=True)
        limit_values = [value for key, value in pairs if key == "limit"]
        if len(limit_values) > 1:
            self.error(400, "invalid limit")
            return
        limit = 20
        if limit_values:
            raw = limit_values[0]
            if not re.fullmatch(r"[0-9]+", raw):
                self.error(400, "invalid limit")
                return
            limit = int(raw, 10)
            if not 1 <= limit <= 100:
                self.error(400, "invalid limit")
                return

        connection = self.database()
        try:
            inbox = connection.execute(
                "SELECT 1 FROM inboxes WHERE id = ?", (inbox_id,)
            ).fetchone()
            if inbox is None:
                self.error(404, "not found")
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
        self.send_json(200, {"events": [dict(row) for row in rows]})

    def get_event(self, event_id):
        connection = self.database()
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
            self.error(404, "not found")
            return
        try:
            headers = json.loads(row["headers_json"])
        except (ValueError, TypeError):
            headers = {}
        result = {
            "id": row["id"],
            "inbox_id": row["inbox_id"],
            "external_id": row["external_id"],
            "received_at": row["received_at"],
            "body_sha256": row["body_sha256"],
            "body": row["body"],
            "headers": headers,
        }
        self.send_json(200, result)

    def get_retention(self, inbox_id):
        connection = self.database()
        try:
            inbox = connection.execute(
                "SELECT 1 FROM inboxes WHERE id = ?", (inbox_id,)
            ).fetchone()
            if inbox is None:
                self.error(404, "not found")
                return
            row = connection.execute(
                "SELECT max_age_seconds FROM retention_policies WHERE inbox_id = ?",
                (inbox_id,),
            ).fetchone()
        finally:
            connection.close()
        value = None if row is None else row["max_age_seconds"]
        self.send_json(200, {"max_age_seconds": value})

    def put_retention(self, inbox_id):
        value, ok = self.read_json()
        if not ok:
            return
        if not isinstance(value, dict) or set(value.keys()) != {"max_age_seconds"}:
            self.error(400, "invalid retention policy", close=True)
            return
        max_age = value["max_age_seconds"]
        if max_age is not None:
            if isinstance(max_age, bool) or not isinstance(max_age, int):
                self.error(400, "invalid retention policy", close=True)
                return
            if not 1 <= max_age <= 2592000:
                self.error(400, "invalid retention policy", close=True)
                return

        connection = self.database()
        try:
            connection.execute("BEGIN IMMEDIATE")
            inbox = connection.execute(
                "SELECT 1 FROM inboxes WHERE id = ?", (inbox_id,)
            ).fetchone()
            if inbox is None:
                connection.execute("ROLLBACK")
                self.error(404, "not found")
                return
            if max_age is None:
                connection.execute(
                    "DELETE FROM retention_policies WHERE inbox_id = ?", (inbox_id,)
                )
            else:
                connection.execute(
                    """
                    INSERT INTO retention_policies (inbox_id, max_age_seconds)
                    VALUES (?, ?)
                    ON CONFLICT(inbox_id) DO UPDATE SET max_age_seconds = excluded.max_age_seconds
                    """,
                    (inbox_id, max_age),
                )
            connection.execute("COMMIT")
        except Exception:
            if connection.in_transaction:
                connection.execute("ROLLBACK")
            raise
        finally:
            connection.close()
        self.send_json(200, {"max_age_seconds": max_age})

    def expire_events(self, inbox_id):
        value, ok = self.read_json(allow_empty=True)
        if not ok:
            return
        if not isinstance(value, dict) or any(key != "limit" for key in value):
            self.error(400, "invalid expiration request", close=True)
            return
        limit = value.get("limit", 100)
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 100:
            self.error(400, "invalid expiration limit", close=True)
            return

        batch_now = utc_now()
        connection = self.database()
        deleted = 0
        try:
            connection.execute("BEGIN IMMEDIATE")
            inbox = connection.execute(
                "SELECT 1 FROM inboxes WHERE id = ?", (inbox_id,)
            ).fetchone()
            if inbox is None:
                connection.execute("ROLLBACK")
                self.error(404, "not found")
                return
            policy = connection.execute(
                "SELECT max_age_seconds FROM retention_policies WHERE inbox_id = ?",
                (inbox_id,),
            ).fetchone()
            if policy is not None:
                cutoff = iso_utc(batch_now - timedelta(seconds=policy["max_age_seconds"]))
                rows = connection.execute(
                    """
                    SELECT id FROM events
                    WHERE inbox_id = ?
                      AND julianday(received_at) <= julianday(?)
                    ORDER BY julianday(received_at) ASC, id ASC
                    LIMIT ?
                    """,
                    (inbox_id, cutoff, limit),
                ).fetchall()
                ids = [row["id"] for row in rows]
                if ids:
                    placeholders = ",".join("?" for _ in ids)
                    cursor = connection.execute(
                        "DELETE FROM events WHERE inbox_id = ? AND id IN (" + placeholders + ")",
                        [inbox_id] + ids,
                    )
                    deleted = cursor.rowcount
            connection.execute("COMMIT")
        except Exception:
            if connection.in_transaction:
                connection.execute("ROLLBACK")
            raise
        finally:
            connection.close()
        self.send_json(200, {"deleted": deleted})


def main():
    admin_token = os.environ.get("PHOENIX_ADMIN_TOKEN")
    if not admin_token:
        print("PHOENIX_ADMIN_TOKEN is required", file=sys.stderr)
        raise SystemExit(1)

    port_text = os.environ.get("PORT", str(DEFAULT_PORT))
    try:
        port = int(port_text)
    except ValueError:
        print("PORT must be an integer", file=sys.stderr)
        raise SystemExit(1)
    if not 0 <= port <= 65535:
        print("PORT is out of range", file=sys.stderr)
        raise SystemExit(1)

    db_path = os.environ.get("PHOENIX_DB_PATH", DEFAULT_DB_PATH)
    try:
        migrate_database(db_path)
    except Exception as exc:
        print("database initialization failed: " + str(exc), file=sys.stderr)
        raise SystemExit(1)

    server = PhoenixServer(("127.0.0.1", port), Handler, db_path, admin_token)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
