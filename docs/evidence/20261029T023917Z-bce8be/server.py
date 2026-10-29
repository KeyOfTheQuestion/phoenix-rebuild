"""AI-assisted reference pilot for contract 002; not an independent AI reconstruction."""
import hashlib
import hmac
import json
import os
import re
import secrets
import sqlite3
from datetime import datetime, timedelta, timezone
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


def initialize():
    # Do not use executescript: its implicit commit would break migration atomicity.
    with connect() as db:
        db.execute("BEGIN IMMEDIATE")
        version = db.execute("PRAGMA user_version").fetchone()[0]
        if version not in (0, 1, 2):
            raise RuntimeError("Unsupported database version; no migration performed")
        for statement in SCHEMA.split(";"):
            if statement.strip():
                db.execute(statement)
        expected = {
            "inboxes": {"id", "name", "token_sha256", "created_at"},
            "events": {"id", "inbox_id", "external_id", "received_at", "headers_json", "body", "body_sha256"},
        }
        for table, columns in expected.items():
            actual = {row[1]: row[2].upper() for row in db.execute("PRAGMA table_info(" + table + ")")}
            if actual != {column: "TEXT" for column in columns}:
                raise RuntimeError("Unsupported base schema; no migration performed")
        db.execute("CREATE TABLE IF NOT EXISTS retention_policies ("
                   "inbox_id TEXT PRIMARY KEY REFERENCES inboxes(id), "
                   "max_age_seconds INTEGER NOT NULL CHECK(max_age_seconds BETWEEN 1 AND 2592000))")
        db.execute("PRAGMA user_version=2")


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
            return self.send_json(200, {"status": "ok", "schema_version": 2})
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
                    db.execute("INSERT INTO inboxes(id,name,token_sha256,created_at) VALUES (?, ?, ?, ?)",
                               (item["id"], name, digest(token), item["created_at"]))
                    db.commit()
                    return self.send_json(201, dict(item, token=token))
                if self.command == "GET":
                    rows = db.execute("SELECT id,name,created_at FROM inboxes ORDER BY created_at DESC,id DESC")
                    return self.send_json(200, {"inboxes": [dict(row) for row in rows]})
                return self.error(405, "Method not allowed")
            retention = re.fullmatch(r"/api/inboxes/([^/]+)/retention", path)
            expiry = re.fullmatch(r"/api/inboxes/([^/]+)/expire", path)
            if retention or expiry:
                allowed = ("GET", "PUT") if retention else ("POST",)
                if self.command not in allowed:
                    return self.error(405, "Method not allowed")
                inbox = (retention or expiry).group(1)
                if db.execute("SELECT 1 FROM inboxes WHERE id=?", (inbox,)).fetchone() is None:
                    return self.error(404, "Not found")
                if retention and self.command == "GET":
                    policy = db.execute("SELECT max_age_seconds FROM retention_policies WHERE inbox_id=?", (inbox,)).fetchone()
                    return self.send_json(200, {"max_age_seconds": policy[0] if policy else None})
                raw = self.body()
                data = json.loads(raw) if raw else {}
                if retention:
                    if not isinstance(data, dict) or set(data) != {"max_age_seconds"}:
                        return self.error(400, "Supply only max_age_seconds")
                    age = data["max_age_seconds"]
                    if age is not None and (type(age) is not int or not 1 <= age <= 2592000):
                        return self.error(400, "max_age_seconds must be null or an integer in 1..2592000")
                    if age is None:
                        db.execute("DELETE FROM retention_policies WHERE inbox_id=?", (inbox,))
                    else:
                        db.execute("INSERT INTO retention_policies(inbox_id,max_age_seconds) VALUES (?,?) "
                                   "ON CONFLICT(inbox_id) DO UPDATE SET max_age_seconds=excluded.max_age_seconds", (inbox, age))
                    db.commit()
                    return self.send_json(200, {"max_age_seconds": age})
                if not isinstance(data, dict) or set(data) - {"limit"}:
                    return self.error(400, "Supply only an optional limit")
                limit = data.get("limit", 100)
                if type(limit) is not int or not 1 <= limit <= 100:
                    return self.error(400, "limit must be an integer in 1..100")
                # Serialize the policy read and bounded deletion with concurrent writers.
                db.execute("BEGIN IMMEDIATE")
                policy = db.execute("SELECT max_age_seconds FROM retention_policies WHERE inbox_id=?", (inbox,)).fetchone()
                deleted = 0
                if policy:
                    cutoff = (datetime.now(timezone.utc) - timedelta(seconds=policy[0])).isoformat()
                    result = db.execute("DELETE FROM events WHERE inbox_id=? AND id IN ("
                                        "SELECT id FROM events WHERE inbox_id=? AND julianday(received_at)<=julianday(?) "
                                        "ORDER BY julianday(received_at),id LIMIT ?)", (inbox, inbox, cutoff, limit))
                    deleted = result.rowcount
                db.commit()
                return self.send_json(200, {"deleted": deleted})
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
                    db.execute("INSERT INTO events(id,inbox_id,external_id,received_at,headers_json,body,body_sha256) VALUES (?,?,?,?,?,?,?)", (
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
    initialize()
    ThreadingHTTPServer(("127.0.0.1", int(os.environ.get("PORT", "8787"))), Handler).serve_forever()
