"""A reviewable baseline for contract 001; AI rebuilds never receive this file."""
import hashlib
import hmac
import json
import os
import re
import secrets
import sqlite3
from datetime import datetime, timezone
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

DB = os.environ.get("PHOENIX_DB_PATH", "phoenix.sqlite")
ADMIN = os.environ.get("PHOENIX_ADMIN_TOKEN", "")
SCHEMA = """
CREATE TABLE IF NOT EXISTS inboxes (
 id TEXT PRIMARY KEY, name TEXT NOT NULL, token_sha256 TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS events (
 id TEXT PRIMARY KEY, inbox_id TEXT NOT NULL REFERENCES inboxes(id), external_id TEXT,
 received_at TEXT NOT NULL, headers_json TEXT NOT NULL, body TEXT NOT NULL, body_sha256 TEXT NOT NULL,
 UNIQUE(inbox_id, external_id)
);
"""


@contextmanager
def connect():
    db = sqlite3.connect(DB, timeout=10)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys=ON")
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def now():
    return datetime.now(timezone.utc).isoformat()


def digest(value):
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args):
        pass  # Hook URLs carry credentials; even default access logs would leak them.

    def send_json(self, code, value):
        raw = json.dumps(value, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(raw)

    def error(self, code, message):
        self.close_connection = True
        self.send_json(code, {"error": message})

    def body(self):
        if self.headers.get("Transfer-Encoding") is not None:
            raise ValueError("Transfer-Encoding is unsupported")
        lengths = self.headers.get_all("Content-Length", [])
        if len(lengths) > 1:
            raise ValueError("Ambiguous Content-Length")
        length = lengths[0] if lengths else "0"
        if not re.fullmatch(r"[0-9]+", length):
            raise ValueError("Invalid Content-Length")
        size = int(length)
        if size > 65536:
            raise OverflowError("Body exceeds 65536 bytes")
        return self.rfile.read(size).decode("utf-8")

    def handle_one_request(self):
        self.connection.settimeout(10)
        super().handle_one_request()

    def route(self):
        parsed = urlsplit(self.path)
        path = parsed.path
        if path.startswith("/api/"):
            auth = self.headers.get_all("Authorization", [])
            expected = "Bearer " + ADMIN
            if len(auth) != 1 or not hmac.compare_digest(auth[0].encode(), expected.encode()):
                return self.error(401, "Unauthorized")
        if self.command == "GET" and path == "/health":
            return self.send_json(200, {"status": "ok", "schema_version": 1})
        with connect() as db:
            if path == "/api/inboxes":
                if self.command == "POST":
                    data = json.loads(self.body())
                    if not isinstance(data, dict) or not isinstance(data.get("name"), str):
                        return self.error(400, "name must be a string")
                    name = data["name"].strip()
                    if not 1 <= len(name) <= 80:
                        return self.error(400, "name must contain 1..80 characters")
                    item = {"id": secrets.token_hex(16), "name": name, "created_at": now()}
                    token = secrets.token_urlsafe(32)
                    db.execute("INSERT INTO inboxes VALUES (?, ?, ?, ?)",
                               (item["id"], name, digest(token), item["created_at"]))
                    db.commit()
                    return self.send_json(201, dict(item, token=token))
                if self.command == "GET":
                    rows = db.execute("SELECT id,name,created_at FROM inboxes ORDER BY created_at DESC,id DESC")
                    return self.send_json(200, {"inboxes": [dict(row) for row in rows]})
                return self.error(405, "Method not allowed")
            hook = re.fullmatch(r"/hooks/([^/]+)/([^/]+)", path)
            if hook:
                if self.command != "POST":
                    return self.error(405, "Method not allowed")
                inbox, token = hook.groups()
                row = db.execute("SELECT token_sha256 FROM inboxes WHERE id=?", (inbox,)).fetchone()
                if row is None or not hmac.compare_digest(row[0], digest(token)):
                    return self.error(404, "Not found")
                text = self.body()
                key = self.headers.get("X-Webhook-Id") or None
                selected = {key: self.headers[key] for key in ("content-type", "x-webhook-id", "x-event-type")
                            if self.headers.get(key) is not None}
                event_id = secrets.token_hex(16)
                try:
                    db.execute("INSERT INTO events VALUES (?,?,?,?,?,?,?)", (
                        event_id, inbox, key, now(), json.dumps(selected), text, digest(text)))
                    db.commit()
                except sqlite3.IntegrityError:
                    db.rollback()
                    old = db.execute("SELECT id,body FROM events WHERE inbox_id=? AND external_id=?", (inbox, key)).fetchone()
                    if old is None:
                        raise
                    if old["body"] != text:
                        return self.error(409, "Idempotency key reused with a different body")
                    return self.send_json(200, {"id": old["id"], "duplicate": True})
                return self.send_json(201, {"id": event_id, "duplicate": False})
            listing = re.fullmatch(r"/api/inboxes/([^/]+)/events", path)
            if listing:
                if self.command != "GET":
                    return self.error(405, "Method not allowed")
                inbox = listing.group(1)
                if db.execute("SELECT 1 FROM inboxes WHERE id=?", (inbox,)).fetchone() is None:
                    return self.error(404, "Not found")
                limits = parse_qs(parsed.query, keep_blank_values=True).get("limit", ["20"])
                if len(limits) != 1 or not re.fullmatch(r"[0-9]+", limits[0]) or not 1 <= int(limits[0]) <= 100:
                    return self.error(400, "limit must be an integer between 1 and 100")
                rows = db.execute("SELECT id,inbox_id,external_id,received_at,body_sha256 FROM events "
                                  "WHERE inbox_id=? ORDER BY received_at DESC,id DESC LIMIT ?", (inbox, int(limits[0])))
                return self.send_json(200, {"events": [dict(row) for row in rows]})
            detail = re.fullmatch(r"/api/events/([^/]+)", path)
            if detail:
                if self.command != "GET":
                    return self.error(405, "Method not allowed")
                row = db.execute("SELECT * FROM events WHERE id=?", (detail.group(1),)).fetchone()
                if row is None:
                    return self.error(404, "Not found")
                event = dict(row)
                event["headers"] = json.loads(event.pop("headers_json"))
                return self.send_json(200, event)
        return self.error(404, "Not found")

    def dispatch(self):
        try:
            return self.route()
        except OverflowError:
            return self.error(413, "Body exceeds 65536 bytes")
        except (ValueError, UnicodeError):
            return self.error(400, "Invalid request")
        except sqlite3.Error:
            return self.error(503, "Storage unavailable")

    do_GET = dispatch
    do_POST = dispatch
    do_PUT = dispatch
    do_DELETE = dispatch
    do_PATCH = dispatch
    do_OPTIONS = dispatch
    do_HEAD = dispatch


if __name__ == "__main__":
    if not ADMIN:
        raise SystemExit("PHOENIX_ADMIN_TOKEN must be configured")
    with connect() as connection:
        connection.executescript(SCHEMA)
    ThreadingHTTPServer(("127.0.0.1", int(os.environ.get("PORT", "8787"))), Handler).serve_forever()
