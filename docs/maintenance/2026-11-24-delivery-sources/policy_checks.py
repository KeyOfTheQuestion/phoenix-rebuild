"""Independent files, receivers and journal reads exercise destination policy."""
import contextlib
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from phoenix import __main__ as cli
from phoenix.delivery import DeliveryError
from phoenix.delivery_journal import delivery_history
from phoenix.records import ROOT
from test_delivery import BODY, TOKEN, DeliveryFixture


class PolicyTests(DeliveryFixture, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.directory = tempfile.TemporaryDirectory()
        self.path = Path(self.directory.name) / "synthetic-private-policy.json"
        self.journal = Path(self.directory.name) / "attempts.sqlite3"
        self.allowed = self.destination + "/receive?fixture=1"

    def tearDown(self):
        super().tearDown()
        self.directory.cleanup()

    def policy(self, destinations=None):
        raw = json.dumps({"schema_version": 1, "destinations": destinations or [self.allowed]}).encode()
        self.path.write_bytes(raw)
        return raw

    def protected(self, **kwargs):
        options = {"destination": self.allowed, "policy": self.path}
        options.update(kwargs)
        return self.deliver(**options)

    def test_K01_exact_approved_destination_delivers_and_binds_final_journal(self):
        raw = self.policy()
        report = self.protected(journal=self.journal)
        self.assertEqual((report["status"], report["attempts"]), ("delivered", 1))
        self.assertEqual(report["destination_policy_sha256"], hashlib.sha256(raw).hexdigest())
        request = self.destination_state["requests"][0]
        self.assertEqual((request["path"], request["body"]), ("/receive?fixture=1", BODY.encode()))
        stored = delivery_history(self.journal)[0]["receipt"]
        self.assertEqual(stored["destination_policy_sha256"], report["destination_policy_sha256"])
        for secret in [str(self.path), self.allowed, TOKEN, BODY]:
            self.assertNotIn(secret, json.dumps(report, ensure_ascii=False))
            self.assertNotIn(secret.encode(), self.journal.read_bytes())

    def test_K02_near_matches_refuse_before_http_or_journal_creation(self):
        self.policy()
        for destination in [self.destination + "/receive", self.allowed + "0", self.destination + "/other?fixture=1",
                            self.source + "/receive?fixture=1", self.destination + "/receive?fixture=%31",
                            self.allowed + "&extra=1"]:
            with self.subTest(destination=destination), self.assertRaises(DeliveryError):
                self.protected(destination=destination, journal=self.journal)
        self.assertEqual(self.source_state["requests"], [])
        self.assertEqual(self.destination_state["requests"], [])
        self.assertFalse(self.journal.exists())

    def test_K03_entire_policy_schema_is_strict(self):
        cases = [b"", b"[]", b"null", b"{", b'\xff', b'\xef\xbb\xbf{}',
                 json.dumps({"schema_version": True, "destinations": [self.allowed]}).encode(),
                 json.dumps({"schema_version": 2, "destinations": [self.allowed]}).encode(),
                 json.dumps({"schema_version": 1, "destinations": []}).encode(),
                 json.dumps({"schema_version": 1, "destinations": self.allowed}).encode(),
                 json.dumps({"schema_version": 1, "destinations": [self.allowed], "allow": True}).encode(),
                 json.dumps({"schema_version": 1, "destinations": [self.allowed, self.allowed]}).encode(),
                 json.dumps({"schema_version": 1, "destinations": [self.allowed, None]}).encode(),
                 json.dumps({"schema_version": 1, "destinations": [self.allowed, "http://example.invalid/receive"]}).encode(),
                 ('{"schema_version":2,"schema_version":1,"destinations":[' + json.dumps(self.allowed) + ']}').encode(),
                 ('{"schema_version":1,"destinations":[],"destinations":[' + json.dumps(self.allowed) + ']}').encode(),
                 b"[" * 1500 + b"]" * 1500]
        for raw in cases:
            with self.subTest(raw=raw[:60]):
                self.path.write_bytes(raw)
                with self.assertRaises(DeliveryError):
                    self.protected(journal=self.journal)
        self.assertFalse(self.journal.exists())
        self.assertEqual(self.source_state["requests"], [])
        self.assertEqual(self.destination_state["requests"], [])

    def test_K04_byte_and_entry_limits_are_enforced_at_boundaries(self):
        raw = self.policy([self.allowed] + [self.destination + "/unused/%s" % n for n in range(15)])
        raw += b" " * (16384 - len(raw))
        self.path.write_bytes(raw)
        report = self.protected()
        self.assertEqual(report["destination_policy_sha256"], hashlib.sha256(raw).hexdigest())
        for invalid in [raw + b" ", json.dumps({"schema_version": 1, "destinations": [self.allowed] +
                        [self.destination + "/unused/%s" % n for n in range(16)]}).encode()]:
            self.path.write_bytes(invalid)
            with self.assertRaises(DeliveryError):
                self.protected()
        self.assertEqual(len(self.destination_state["requests"]), 1)

    def test_K05_policy_is_captured_once_and_edits_apply_to_next_invocation(self):
        raw = self.policy()
        self.source_state["on_get"] = lambda: self.policy([self.destination + "/revoked"])
        report = self.protected(journal=self.journal)
        self.assertEqual(report["status"], "delivered")
        self.assertEqual(report["destination_policy_sha256"], hashlib.sha256(raw).hexdigest())
        self.assertNotEqual(self.path.read_bytes(), raw)
        with self.assertRaises(DeliveryError):
            self.protected(journal=self.journal)
        self.assertEqual(len(self.source_state["requests"]), 1)
        self.assertEqual(len(self.destination_state["requests"]), 1)
        self.assertEqual(len(delivery_history(self.journal)), 1)

    def test_K06_policy_hash_survives_rejection_source_error_and_unknown_delivery(self):
        raw = self.policy()
        self.destination_state["status"] = 503
        rejected = self.protected(journal=self.journal)
        self.source_state["status"] = 401
        source_error = self.protected(journal=self.journal)
        self.source_state["status"] = 200
        self.destination_state["delay"] = 3
        unknown = self.protected(journal=self.journal, timeout=0.8)
        self.assertEqual([r["status"] for r in [rejected, source_error, unknown]],
                         ["rejected", "source-error", "delivery-unknown"])
        self.assertTrue(all(r["destination_policy_sha256"] == hashlib.sha256(raw).hexdigest()
                            for r in [rejected, source_error, unknown]))
        self.assertTrue(all(r["receipt"]["destination_policy_sha256"] == hashlib.sha256(raw).hexdigest()
                            for r in delivery_history(self.journal)))
        self.assertEqual(len(self.destination_state["requests"]), 2)

    def test_K07_cli_enforces_policy_and_redacts_refusals(self):
        self.policy()
        arguments = ["phoenix", "deliver-event", "--source", self.source, "--event-id", "event-1",
                     "--destination", self.allowed, "--destination-policy", str(self.path)]
        for expected in [0, 2]:
            output, errors = io.StringIO(), io.StringIO()
            with patch("sys.argv", arguments), patch.dict(os.environ, PHOENIX_ADMIN_TOKEN=TOKEN), \
                    contextlib.redirect_stdout(output), contextlib.redirect_stderr(errors):
                self.assertEqual(cli.main(), expected)
            for secret in [TOKEN, self.allowed, str(self.path)]:
                self.assertNotIn(secret, output.getvalue() + errors.getvalue())
            self.policy([self.destination + "/not-approved"])
        self.assertEqual(len(self.destination_state["requests"]), 1)

    def test_K08_unavailable_and_nonregular_files_refuse_before_http(self):
        fifo = Path(self.directory.name) / "policy.fifo"
        os.mkfifo(fifo)
        for path in [self.path, Path(self.directory.name), fifo]:
            with self.subTest(path=str(path)), self.assertRaises(DeliveryError):
                self.protected(policy=path, journal=self.journal)
        self.assertEqual(self.source_state["requests"], [])
        self.assertEqual(self.destination_state["requests"], [])
        self.assertFalse(self.journal.exists())

    def test_K09_repeated_nonregular_refusals_release_file_descriptors(self):
        fifo = Path(self.directory.name) / "policy.fifo"
        os.mkfifo(fifo)
        # Isolate descriptor counting from HTTP fixture threads in the suite.
        program = """
import os
import sys

from phoenix.delivery import DeliveryError
from phoenix.delivery_policy import authorize_destination

before = len(os.listdir('/dev/fd'))
for _ in range(20):
    for path in sys.argv[1:]:
        try:
            authorize_destination(path, 'http://127.0.0.1:12345/receive')
        except DeliveryError:
            continue
        raise AssertionError('Policy reader accepted a nonregular file')
after = len(os.listdir('/dev/fd'))
if after != before:
    raise AssertionError(f'Policy descriptors before: {before}, after: {after}')
"""
        result = subprocess.run(
            [sys.executable, "-c", program, self.directory.name, str(fifo)],
            cwd=ROOT, capture_output=True, text=True, timeout=10,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
