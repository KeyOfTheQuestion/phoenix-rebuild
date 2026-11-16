"""Actual HTTP, SQLite and process interruption checks for local attempt history."""
import concurrent.futures
import contextlib
import hashlib
import io
import json
import os
from pathlib import Path
import signal
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from phoenix import __main__ as cli
from phoenix import delivery_journal as journal
from test_delivery import BODY, TOKEN, DeliveryFixture


class JournalTests(DeliveryFixture, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.directory = tempfile.TemporaryDirectory()
        self.path = Path(self.directory.name) / "attempts.sqlite3"

    def tearDown(self):
        super().tearDown()
        self.directory.cleanup()

    def test_J01_final_receipts_are_private_redacted_and_persistent(self):
        delivered = self.deliver(destination=self.destination + "/receive?secret=synthetic-query", journal=self.path)
        self.destination_state["status"] = 503
        rejected = self.deliver(journal=self.path)
        self.source_state["status"] = 401
        failed = self.deliver(journal=self.path)
        rows = journal.delivery_history(self.path)
        self.assertEqual([r["receipt"]["status"] for r in rows], ["source-error", "rejected", "delivered"])
        self.assertEqual({r["id"] for r in rows}, {r["journal_attempt_id"] for r in [delivered, rejected, failed]})
        self.assertTrue(all(r["journal_saved"] for r in [delivered, rejected, failed]))
        self.assertTrue(all(r["state"] == "finished" for r in rows))
        raw = self.path.read_bytes()
        for secret in [TOKEN, BODY, self.source, self.destination, "synthetic-query"]:
            self.assertNotIn(secret.encode(), raw)
        self.assertEqual(self.path.stat().st_mode & 0o777, 0o600)

    def test_J02_unavailable_or_unrelated_journal_refuses_before_http(self):
        unrelated = self.path
        with contextlib.closing(sqlite3.connect(str(unrelated))) as connection, connection:
            connection.execute("CREATE TABLE user_work (value TEXT)")
            connection.execute("INSERT INTO user_work VALUES ('keep me')")
        original = unrelated.read_bytes()
        for path in [unrelated, Path(self.directory.name), Path(self.directory.name) / "absent" / "db"]:
            with self.subTest(path=str(path)), self.assertRaises(journal.JournalError):
                self.deliver(journal=path)
        self.assertEqual(unrelated.read_bytes(), original)
        self.assertEqual(self.source_state["requests"], [])
        self.assertEqual(self.destination_state["requests"], [])

    def test_J03_history_is_bounded_read_only_and_never_delivers(self):
        with self.assertRaises(journal.JournalError):
            journal.delivery_history(self.path)
        self.assertFalse(self.path.exists())
        first = self.deliver(journal=self.path)
        last = self.deliver(journal=self.path)
        before = self.path.read_bytes()
        self.assertEqual(journal.delivery_history(self.path, 1)[0]["id"], last["journal_attempt_id"])
        for limit in [0, 101, True, "1"]:
            with self.subTest(limit=limit), self.assertRaises(journal.JournalError):
                journal.delivery_history(self.path, limit)
        output = io.StringIO()
        with patch("sys.argv", ["phoenix", "delivery-history", "--journal", str(self.path), "--limit", "1"]), \
                contextlib.redirect_stdout(output):
            self.assertEqual(cli.main(), 0)
        self.assertEqual(json.loads(output.getvalue())["attempts"][0]["id"], last["journal_attempt_id"])
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(len(self.destination_state["requests"]), 2)
        self.assertNotEqual(first["journal_attempt_id"], last["journal_attempt_id"])

    def test_J04_killed_client_retains_unconfirmed_attempt_without_retry(self):
        self.destination_state["delay"] = 3
        process = subprocess.Popen([sys.executable, "-m", "phoenix", "deliver-event", "--source", self.source,
                                    "--event-id", "event-1", "--destination", self.destination,
                                    "--journal", str(self.path), "--timeout", "5"],
                                   env=dict(os.environ, PHOENIX_ADMIN_TOKEN=TOKEN),
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
        try:
            deadline = time.monotonic() + 3
            while not self.destination_state["requests"] and time.monotonic() < deadline:
                time.sleep(0.01)
            self.assertEqual(len(self.destination_state["requests"]), 1)
            rows = journal.delivery_history(self.path)
            self.assertEqual((rows[0]["state"], rows[0]["receipt"]), ("unconfirmed", None))
            os.killpg(process.pid, signal.SIGKILL)
            process.wait(timeout=3)
            self.assertEqual(journal.delivery_history(self.path), rows)
            self.assertEqual(len(self.destination_state["requests"]), 1)
        finally:
            if process.poll() is None:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait(timeout=3)

    def test_J05_concurrent_clients_retain_distinct_attempts(self):
        # Initialize once, then exercise real competing SQLite transactions.
        self.deliver(journal=self.path)
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(lambda _: self.deliver(journal=self.path), range(8)))
        self.assertEqual(len({r["journal_attempt_id"] for r in results}), 8)
        self.assertTrue(all(r["status"] == "delivered" and r["journal_saved"] for r in results))
        self.assertEqual(len(journal.delivery_history(self.path)), 9)
        self.assertEqual(len(self.destination_state["requests"]), 9)

    def test_J06_failed_final_commit_keeps_http_outcome_and_unconfirmed_row(self):
        locks = []
        def lock_after_post():
            connection = sqlite3.connect(str(self.path), check_same_thread=False)
            connection.execute("BEGIN IMMEDIATE")
            locks.append(connection)
        self.destination_state["on_post"] = lock_after_post
        output = io.StringIO()
        arguments = ["phoenix", "deliver-event", "--source", self.source, "--event-id", "event-1",
                     "--destination", self.destination, "--journal", str(self.path)]
        try:
            with patch("sys.argv", arguments), patch.dict(os.environ, PHOENIX_ADMIN_TOKEN=TOKEN), \
                    contextlib.redirect_stdout(output):
                self.assertEqual(cli.main(), 2)
            receipt = json.loads(output.getvalue())
            self.assertEqual((receipt["status"], receipt["http_status"], receipt["attempts"]), ("delivered", 202, 1))
            self.assertIs(receipt["journal_saved"], False)
            self.assertEqual(journal.delivery_history(self.path)[0]["state"], "unconfirmed")
            self.assertEqual(len(self.destination_state["requests"]), 1)
            self.assertNotIn(TOKEN, output.getvalue())
        finally:
            for connection in locks:
                connection.rollback()
                connection.close()

    def test_J07_simultaneous_first_use_publishes_only_initialized_database(self):
        barrier = threading.Barrier(8)
        def send(_):
            barrier.wait(timeout=3)
            return self.deliver(journal=self.path)
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(send, range(8)))
        self.assertTrue(all(r["journal_saved"] and r["status"] == "delivered" for r in results))
        self.assertEqual(len(journal.delivery_history(self.path)), 8)
        self.assertEqual(len(self.destination_state["requests"]), 8)
        self.assertEqual({p.name for p in self.path.parent.iterdir()}, {self.path.name})

    def test_J08_refusing_unrelated_hot_database_preserves_recovery_files(self):
        with contextlib.closing(sqlite3.connect(str(self.path))) as connection, connection:
            connection.execute("CREATE TABLE user_work (value BLOB)")
            connection.executemany("INSERT INTO user_work VALUES (zeroblob(4096))", [()] * 300)
        script = """import os, sqlite3, sys
connection = sqlite3.connect(sys.argv[1])
connection.execute('PRAGMA cache_size=10')
connection.execute('PRAGMA cache_spill=10')
connection.execute('BEGIN IMMEDIATE')
connection.execute('UPDATE user_work SET value=randomblob(4096)')
os._exit(0)
"""
        subprocess.run([sys.executable, "-c", script, str(self.path)], check=True)
        rollback = Path(str(self.path) + "-journal")
        self.assertTrue(rollback.is_file())
        self.assertNotEqual(rollback.read_bytes()[:8], b"\x00" * 8)
        before = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in self.path.parent.iterdir()}
        with self.assertRaises(journal.JournalError):
            self.deliver(journal=self.path)
        self.assertEqual({p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in self.path.parent.iterdir()}, before)
        self.assertEqual(self.source_state["requests"], [])
        self.assertEqual(self.destination_state["requests"], [])

    def test_J09_wal_history_refuses_without_creating_sidecars(self):
        self.deliver(journal=self.path)
        with contextlib.closing(sqlite3.connect(str(self.path))) as connection:
            self.assertEqual(connection.execute("PRAGMA journal_mode=WAL").fetchone()[0], "wal")
        before = {p.name: p.read_bytes() for p in self.path.parent.iterdir()}
        with self.assertRaises(journal.JournalError):
            journal.delivery_history(self.path)
        with self.assertRaises(journal.JournalError):
            self.deliver(journal=self.path)
        self.assertEqual({p.name: p.read_bytes() for p in self.path.parent.iterdir()}, before)
        self.assertEqual(len(self.destination_state["requests"]), 1)
