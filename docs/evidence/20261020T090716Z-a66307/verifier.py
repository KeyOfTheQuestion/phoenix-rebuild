"""Black-box acceptance checks. Never imported by the reconstruction subject."""
import hashlib
import http.client
import json
import os
from pathlib import Path
import secrets
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone


class Subject:
    def __init__(self, candidate, directory):
        self.candidate = Path(candidate).resolve()
        self.directory = Path(directory)
        self.database = self.directory / "subject.sqlite"
        self.token = secrets.token_hex(32)
        self.process = None
        self.port = None
        self.log = None

    def start(self):
        with socket.socket() as reservation:
            reservation.bind(("127.0.0.1", 0))
            self.port = reservation.getsockname()[1]
        environment = {key: os.environ[key] for key in ("PATH", "SYSTEMROOT", "TMPDIR", "LANG") if key in os.environ}
        environment.update(PORT=str(self.port), PHOENIX_DB_PATH=str(self.database),
                           PHOENIX_ADMIN_TOKEN=self.token, PYTHONUNBUFFERED="1")
        self.log = open(self.directory / "subject.log", "ab")
        self.process = subprocess.Popen([sys.executable, str(self.candidate)],
                                        cwd=self.directory, env=environment,
                                        stdout=self.log, stderr=self.log)
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            if self.process.poll() is not None:
                raise RuntimeError("Subject exited during startup (inspect local subject.log)")
            try:
                status, _, _ = self.request("GET", "/health", admin=False)
                if status == 200:
                    return
            except (OSError, http.client.HTTPException):
                pass
            time.sleep(0.05)
        raise RuntimeError("Subject did not become ready within 15 seconds")

    def stop(self):
        if self.process is not None and self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait()
        if self.log:
            self.log.close()

    def request(self, method, path, body=None, headers=None, admin=True):
        supplied = dict(headers or {})
        if admin:
            supplied.setdefault("Authorization", "Bearer " + self.token)
        if isinstance(body, (dict, list)):
            body = json.dumps(body, ensure_ascii=False).encode()
            supplied.setdefault("Content-Type", "application/json")
        elif isinstance(body, str):
            body = body.encode()
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        try:
            conn.request(method, path, body=body, headers=supplied)
            response = conn.getresponse()
            raw = response.read()
            try:
                data = json.loads(raw)
            except (ValueError, UnicodeError):
                raise AssertionError("Response is not UTF-8 JSON (HTTP %s)" % response.status)
            return response.status, data, dict(response.getheaders())
        finally:
            conn.close()


def require(condition, message):
    if not condition:
        raise AssertionError(message)


def evaluate(candidate, kind="reference-verification"):
    """Run on trusted, local candidate code; a process is NOT a security sandbox."""
    source = Path(candidate).read_bytes()
    started = time.monotonic()
    report = {"schema_version": 1, "kind": kind,
              "started_at": datetime.now(timezone.utc).isoformat(),
              "candidate_sha256": hashlib.sha256(source).hexdigest(), "checks": [],
              "execution": "local-subprocess", "evaluation_isolation": "procedural",
              "human_interventions": None if kind == "reference-verification" else 0}
    with tempfile.TemporaryDirectory(prefix="phoenix-verify-") as tmp:
        subject = Subject(candidate, tmp)

        def check(identifier, title, category, callback):
            before = time.monotonic()
            try:
                callback()
                item = dict(id=identifier, title=title, category=category, passed=True)
            except Exception as error:
                # Avoid publishing credentials or data through exception reprs.
                message = str(error).replace(subject.token, "[redacted]")
                item = dict(id=identifier, title=title, category=category,
                            passed=False, error=message[:400])
            item["duration_ms"] = round((time.monotonic() - before) * 1000, 2)
            report["checks"].append(item)

        context = {}

        def expect(method, path, status, body=None, headers=None, admin=True):
            actual, data, response_headers = subject.request(method, path, body, headers, admin)
            require(actual == status, "%s %s: expected HTTP %s, got %s" % (method, path.split("?")[0].split("/hooks/")[0] or "/hooks/[redacted]", status, actual))
            require("application/json" in response_headers.get("Content-Type", ""), "Missing JSON content type")
            if status >= 400:
                require(isinstance(data, dict) and isinstance(data.get("error"), str), "Errors need an error string")
            return data

        def create():
            item = expect("POST", "/api/inboxes", 201, {"name": "  Phoenix lab  "})
            require(item["name"] == "Phoenix lab", "Name was not trimmed")
            require(len(item["token"]) >= 43, "Token needs at least 32 random bytes")
            require(isinstance(item["id"], str) and item["created_at"], "Missing inbox metadata")
            context["inbox"] = item
            context["hook"] = "/hooks/%s/%s" % (item["id"], item["token"])
            context["events"] = "/api/inboxes/%s/events" % item["id"]

        def auth():
            for route in ("/api/inboxes", "/api/events/missing", "/api/inboxes/missing/events"):
                expect("GET", route, 401, admin=False)
                expect("GET", route, 401, headers={"Authorization": "Bearer incorrect"})
            expect("POST", "/api/inboxes", 401, {"name": "No access"}, admin=False)

        def capture():
            body = ' {"message":"你好, Phoenix 🔥","amount":42}\n'
            item = expect("POST", context["hook"], 201, body,
                          {"Content-Type": "application/json", "X-Webhook-Id": "event-001",
                           "X-Event-Type": "build.completed", "Cookie": "private-cookie",
                           "Authorization": "Bearer private-header"}, admin=False)
            require(item["duplicate"] is False, "First event should be new")
            context.update(event_id=item["id"], original_body=body)

        def original():
            event = expect("GET", "/api/events/" + context["event_id"], 200)
            require(event["body"] == context["original_body"], "Body bytes were not preserved")
            require(event["body_sha256"] == hashlib.sha256(event["body"].encode()).hexdigest(), "Incorrect body digest")
            require(event["inbox_id"] == context["inbox"]["id"] and event["external_id"] == "event-001", "Incorrect event metadata")
            require(event["headers"]["x-event-type"] == "build.completed", "Allowed event header was lost")
            require(set(event["headers"]) <= {"content-type", "x-webhook-id", "x-event-type"}, "Sensitive or unknown header persisted")
            require(context["inbox"]["token"] not in json.dumps(event), "Hook credential leaked in event")

        def duplicate():
            result = expect("POST", context["hook"], 200, context["original_body"],
                            {"X-Webhook-Id": "event-001"}, admin=False)
            require(result == {"id": context["event_id"], "duplicate": True}, "Duplicate did not return original event")
            expect("POST", context["hook"], 409, "changed", {"X-Webhook-Id": "event-001"}, admin=False)

        def concurrent():
            def send(_):
                return subject.request("POST", context["hook"], "one logical delivery",
                                       {"X-Webhook-Id": "concurrent-event"}, admin=False)
            with ThreadPoolExecutor(max_workers=8) as pool:
                results = list(pool.map(send, range(12)))
            require(sum(r[0] == 201 for r in results) == 1, "Concurrent key must produce exactly one new event")
            require(all(r[0] in (200, 201) for r in results), "Concurrent duplicate request failed")
            require(len({r[1]["id"] for r in results}) == 1, "Concurrent duplicate ids differ")

        def separate():
            first = expect("POST", context["hook"], 201, "same", admin=False)
            second = expect("POST", context["hook"], 201, "same", admin=False)
            require(first["id"] != second["id"], "Requests without keys must be separate")

        def isolation():
            other = expect("POST", "/api/inboxes", 201, {"name": "Another inbox"})
            path = "/hooks/%s/%s" % (other["id"], other["token"])
            result = expect("POST", path, 201, "different inbox", {"X-Webhook-Id": "event-001"}, admin=False)
            require(result["id"] != context["event_id"], "Key incorrectly shared across inboxes")

        def pagination():
            result = expect("GET", context["events"] + "?limit=1", 200)["events"]
            require(len(result) == 1 and "body" not in result[0] and "headers" not in result[0], "Event list is not a summary or limit failed")
            all_events = expect("GET", context["events"], 200)["events"]
            require(len(all_events) == 4, "Expected exactly four persisted events")
            ordered = sorted(all_events, key=lambda e: (e["received_at"], e["id"]), reverse=True)
            require(all_events == ordered, "Events not ordered newest first")
            for query in ("limit=0", "limit=101", "limit=abc", "limit=", "limit=1&limit=2"):
                expect("GET", context["events"] + "?" + query, 400)

        def names():
            for value in ({}, {"name": " "}, {"name": 42}, {"name": "x" * 81}, [], None):
                expect("POST", "/api/inboxes", 400, "null" if value is None else value)
            expect("POST", "/api/inboxes", 400, "{broken")

        def limits():
            expect("POST", context["hook"], 413, b"x" * 65537, admin=False)
            expect("POST", context["hook"], 400, b"\xff", admin=False)
            for length in ("-1", "not-a-number"):
                expect("POST", context["hook"], 400, b"", {"Content-Length": length}, admin=False)
            expect("POST", context["hook"], 400, b"", {"Transfer-Encoding": "chunked"}, admin=False)

        def plain():
            event = expect("POST", context["hook"], 201, "not JSON at all\n", admin=False)
            require(expect("GET", "/api/events/" + event["id"], 200)["body"] == "not JSON at all\n", "Plain text changed")
            empty = expect("POST", context["hook"], 201, "", admin=False)
            require(expect("GET", "/api/events/" + empty["id"], 200)["body"] == "", "Empty body not supported")

        def privacy():
            result = expect("GET", "/api/inboxes", 200)
            raw = json.dumps(result)
            require("token" not in raw, "Inbox listing exposes tokens or hashes")
            with sqlite3.connect(subject.database) as db:
                stored = db.execute("SELECT token_sha256 FROM inboxes WHERE id=?", (context["inbox"]["id"],)).fetchone()
                require(stored[0] == hashlib.sha256(context["inbox"]["token"].encode()).hexdigest(), "Token not hashed using schema contract")
            subject.log.flush()
            log = (Path(tmp) / "subject.log").read_text(errors="replace")
            require(context["inbox"]["token"] not in log and context["original_body"] not in log, "Access logs expose hook credentials or bodies")

        def restart():
            subject.stop()
            subject.start()
            original()
            duplicate()

        def legacy():
            subject.stop()
            with sqlite3.connect(subject.database) as db:
                db.execute("INSERT INTO inboxes VALUES (?,?,?,?)", (
                    "legacy-inbox", "Previous build", hashlib.sha256(b"previous-token").hexdigest(), "2025-01-01T00:00:00+00:00"))
                db.execute("INSERT INTO events VALUES (?,?,?,?,?,?,?)", (
                    "legacy-event", "legacy-inbox", "legacy-key", "2025-01-01T00:00:00+00:00",
                    '{"content-type":"text/plain"}', "created by a previous build", hashlib.sha256(b"created by a previous build").hexdigest()))
            subject.start()
            event = expect("GET", "/api/events/legacy-event", 200)
            require(event["body"] == "created by a previous build", "Previous schema data was lost")
            duplicate_event = expect("POST", "/hooks/legacy-inbox/previous-token", 200,
                                     "created by a previous build", {"X-Webhook-Id": "legacy-key"}, admin=False)
            require(duplicate_event["id"] == "legacy-event", "Legacy idempotency key was lost")

        try:
            check("C01", "Service boots from its documented interface", "contract", subject.start)
            if not report["checks"][-1]["passed"]:
                return finish(report, started)
            check("C02", "Health exposes the schema version", "contract", lambda: require(
                expect("GET", "/health", 200, admin=False) == {"status": "ok", "schema_version": 1}, "Incorrect health payload"))
            check("S01", "Administrative routes reject missing and wrong credentials", "security", auth)
            check("C03", "Create a named inbox with an unpredictable token", "contract", create)
            if "inbox" not in context:
                return finish(report, started)
            check("C04", "Capture a real UTF-8 webhook request", "contract", capture)
            check("C05", "Preserve body, metadata, and safe headers", "contract", original)
            check("R01", "Deduplicate deliveries and reject body conflicts", "reliability", duplicate)
            check("R02", "Concurrent retries create one logical event", "reliability", concurrent)
            check("R03", "Unkeyed deliveries remain independent", "reliability", separate)
            check("R04", "Idempotency keys are scoped to an inbox", "reliability", isolation)
            check("C06", "Bound pagination and list newest events first", "contract", pagination)
            check("S02", "Reject malformed inbox input", "security", names)
            check("S03", "Reject oversized and malformed request bodies", "security", limits)
            check("C07", "Accept non-JSON and empty webhook bodies", "contract", plain)
            check("S04", "Keep credentials out of storage responses and logs", "security", privacy)
            check("S05", "Reject incorrect hook tokens without revealing inboxes", "security", lambda: (
                expect("POST", "/hooks/%s/wrong" % context["inbox"]["id"], 404, "body", admin=False),
                expect("POST", "/hooks/missing/wrong", 404, "body", admin=False)))
            check("C08", "Unknown resources and unsupported methods return JSON", "contract", lambda: (
                expect("GET", "/not-found", 404, admin=False), expect("GET", "/api/events/missing", 404),
                expect("GET", "/api/inboxes/missing/events", 404), expect("DELETE", "/api/inboxes", 405)))
            check("D01", "Events and idempotency survive process restart", "compatibility", restart)
            check("D02", "Read and extend a previous build's schema fixture", "compatibility", legacy)
        finally:
            subject.stop()
    return finish(report, started)


def finish(report, started):
    report["duration_seconds"] = round(time.monotonic() - started, 3)
    report["passed"] = sum(check["passed"] for check in report["checks"])
    report["total"] = len(report["checks"])
    report["status"] = "passed" if report["passed"] == 19 and report["total"] == 19 else "failed"
    return report
