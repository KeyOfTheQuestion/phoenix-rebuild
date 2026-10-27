"""Contract-002 reference controls are separate from the public AI ledger."""
import contextlib
import io
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch

from phoenix import __main__ as cli
from phoenix.records import ROOT
from phoenix.retention import CHECK_IDS, evaluate_retention
from phoenix.verify import Subject

REFERENCE = ROOT / "examples/reference/server.py"
RETENTION = ROOT / "examples/retention/server.py"


class RetentionPilotTests(unittest.TestCase):
    def evaluate_changed(self, source):
        with tempfile.TemporaryDirectory() as temporary:
            candidate = Path(temporary) / "server.py"
            candidate.write_text(source)
            return evaluate_retention(candidate, REFERENCE)

    def test_reference_migrates_latest_successful_ai_data_and_passes_the_focused_pilot(self):
        before = (ROOT / "docs/runs.json").read_bytes()
        rows = json.loads(before)
        latest = next(r for r in rows if r["kind"] == "ai-rebuild" and r["status"] == "passed")
        predecessor = ROOT / "docs/evidence" / latest["id"] / "server.py"
        report = evaluate_retention(RETENTION, predecessor)
        self.assertEqual(report["status"], "passed", report)
        self.assertEqual(report["passed"], len(CHECK_IDS))
        self.assertEqual({c["id"] for c in report["checks"]}, CHECK_IDS)
        self.assertEqual(report["kind"], "retention-reference-verification")
        self.assertIn("SQLite", report["expiry_fixture"])
        self.assertEqual((ROOT / "docs/runs.json").read_bytes(), before)

    def test_an_always_200_candidate_cannot_claim_migration_success(self):
        report = self.evaluate_changed('''import json,os
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
class Handler(BaseHTTPRequestHandler):
 def log_message(self,*args): pass
 def do_GET(self):
  raw=json.dumps({"status":"ok","schema_version":2}).encode()
  self.send_response(200);self.send_header("Content-Type","application/json");self.end_headers();self.wfile.write(raw)
 do_POST=do_GET
ThreadingHTTPServer(("127.0.0.1",int(os.environ["PORT"])),Handler).serve_forever()
''')
        self.assertEqual(report["status"], "failed")
        self.assertFalse(next(c for c in report["checks"] if c["id"] == "M02")["passed"])

    def test_destructive_migration_is_detected_before_expiry_fixture_aging(self):
        source = '''import os,sqlite3,runpy
with sqlite3.connect(os.environ["PHOENIX_DB_PATH"]) as db:
 db.execute("DROP TABLE IF EXISTS events")
 db.execute("DROP TABLE IF EXISTS inboxes")
runpy.run_path(%r,run_name="__main__")
''' % str(RETENTION)
        report = self.evaluate_changed(source)
        self.assertEqual(report["status"], "failed")
        self.assertFalse(next(c for c in report["checks"] if c["id"] == "M02")["passed"])
        self.assertNotIn("F01", {c["id"] for c in report["checks"]})

    def test_no_op_expiry_fails_actual_deletion_checks(self):
        source = RETENTION.read_text()
        predicate = "julianday(received_at)<=julianday(?)"
        self.assertIn(predicate, source)
        report = self.evaluate_changed(source.replace(predicate, "0 AND " + predicate))
        self.assertEqual(report["status"], "failed")
        self.assertFalse(next(c for c in report["checks"] if c["id"] == "E01")["passed"])

    def test_expiry_that_also_deletes_recent_events_is_detected(self):
        source = RETENTION.read_text()
        report = self.evaluate_changed(source.replace("julianday(received_at)<=julianday(?)", "(julianday(?) IS NOT NULL)"))
        self.assertEqual(report["status"], "failed")
        self.assertFalse(next(c for c in report["checks"] if c["id"] == "E03")["passed"])

    def test_unsupported_future_version_is_refused_without_mutating_old_data(self):
        with tempfile.TemporaryDirectory() as temporary:
            old = Subject(REFERENCE, temporary)
            try:
                old.start()
                status, inbox, _ = old.request("POST", "/api/inboxes", {"name": "Future database fixture"})
                self.assertEqual(status, 201)
                path = "/hooks/%s/%s" % (inbox["id"], inbox["token"])
                self.assertEqual(old.request("POST", path, "keep this body", admin=False)[0], 201)
            finally:
                old.stop()
            with sqlite3.connect(old.database) as db:
                db.execute("PRAGMA user_version=999")
                before = db.execute("SELECT * FROM events").fetchall()
                tables = db.execute("SELECT name,sql FROM sqlite_master WHERE type='table' ORDER BY name").fetchall()
            new = Subject(RETENTION, temporary)
            try:
                with self.assertRaisesRegex(RuntimeError, "exited during startup"):
                    new.start()
            finally:
                new.stop()
            with sqlite3.connect(old.database) as db:
                self.assertEqual(db.execute("PRAGMA user_version").fetchone()[0], 999)
                self.assertEqual(db.execute("SELECT * FROM events").fetchall(), before)
                self.assertEqual(db.execute("SELECT name,sql FROM sqlite_master WHERE type='table' ORDER BY name").fetchall(), tables)

    def test_pilot_cli_reports_pass_fail_separately_without_publishing_ai_evidence(self):
        for status, expected in (("passed", 0), ("failed", 1)):
            with self.subTest(status=status), patch.object(sys, "argv", ["phoenix", "verify-retention"]), \
                    patch("phoenix.retention.evaluate_retention", return_value={"status": status}), \
                    patch.object(cli, "publish") as publish, contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(cli.main(), expected)
                publish.assert_not_called()

    def test_mid_migration_error_rolls_back_the_new_table_and_version(self):
        with tempfile.TemporaryDirectory() as temporary:
            old = Subject(REFERENCE, temporary)
            try:
                old.start()
                status, inbox, _ = old.request("POST", "/api/inboxes", {"name": "Rollback fixture"})
                self.assertEqual(status, 201)
                path = "/hooks/%s/%s" % (inbox["id"], inbox["token"])
                self.assertEqual(old.request("POST", path, "preserve on failure", admin=False)[0], 201)
            finally:
                old.stop()
            with sqlite3.connect(old.database) as db:
                before = {table: db.execute("SELECT * FROM " + table).fetchall() for table in ("inboxes", "events")}
                version = db.execute("PRAGMA user_version").fetchone()[0]
            source = RETENTION.read_text()
            boundary = 'db.execute("PRAGMA user_version=2")'
            self.assertIn(boundary, source)
            candidate = Path(temporary) / "fault_injected.py"
            candidate.write_text(source.replace(boundary, 'raise RuntimeError("synthetic failure after policy DDL")'))
            failed = Subject(candidate, temporary)
            try:
                with self.assertRaisesRegex(RuntimeError, "exited during startup"):
                    failed.start()
            finally:
                failed.stop()
            with sqlite3.connect(old.database) as db:
                self.assertEqual(db.execute("PRAGMA user_version").fetchone()[0], version)
                self.assertIsNone(db.execute("SELECT name FROM sqlite_master WHERE name='retention_policies'").fetchone())
                for table, values in before.items():
                    self.assertEqual(db.execute("SELECT * FROM " + table).fetchall(), values)

    def test_equivalent_v1_column_order_is_preserved_and_named_writes_keep_working(self):
        # Deliberately reordered SQL fixture; this is separate from HTTP migration evidence.
        with tempfile.TemporaryDirectory() as temporary:
            subject = Subject(RETENTION, temporary)
            with sqlite3.connect(subject.database) as db:
                db.executescript("""
CREATE TABLE inboxes (name TEXT NOT NULL, id TEXT PRIMARY KEY, created_at TEXT NOT NULL, token_sha256 TEXT NOT NULL);
CREATE TABLE events (body TEXT NOT NULL, id TEXT PRIMARY KEY, inbox_id TEXT NOT NULL REFERENCES inboxes(id),
 external_id TEXT, received_at TEXT NOT NULL, headers_json TEXT NOT NULL, body_sha256 TEXT NOT NULL, UNIQUE(inbox_id,external_id));
""")
                before = {table: [r[1] for r in db.execute("PRAGMA table_info(" + table + ")")]
                          for table in ("inboxes", "events")}
            try:
                subject.start()
                status, inbox, _ = subject.request("POST", "/api/inboxes", {"name": "Reordered columns"})
                self.assertEqual(status, 201)
                path = "/hooks/%s/%s" % (inbox["id"], inbox["token"])
                status, event, _ = subject.request("POST", path, "named writes preserve columns", admin=False)
                self.assertEqual(status, 201)
                status, detail, _ = subject.request("GET", "/api/events/" + event["id"])
                self.assertEqual(status, 200)
                self.assertEqual(detail["body"], "named writes preserve columns")
                self.assertEqual(detail["inbox_id"], inbox["id"])
            finally:
                subject.stop()
            with sqlite3.connect(subject.database) as db:
                after = {table: [r[1] for r in db.execute("PRAGMA table_info(" + table + ")")]
                         for table in ("inboxes", "events")}
            self.assertEqual(after, before)


if __name__ == "__main__":
    unittest.main()
