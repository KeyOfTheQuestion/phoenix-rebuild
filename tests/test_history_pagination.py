"""Actual SQLite pages and CLI continuations; fixtures are synthetic local rows."""
import base64
import contextlib
from datetime import datetime, timedelta, timezone
import hashlib
import io
import json
from pathlib import Path
import tempfile
import traceback
import unittest
from unittest.mock import patch

from phoenix import __main__ as cli
from phoenix import delivery_journal as journal


class HistoryPageTests(unittest.TestCase):
    def test_H09_lookup_finds_an_attempt_beyond_the_first_page(self):
        self.seed(123)
        identifier = "%032x" % 1
        before = self.path.read_bytes()
        attempt = journal.delivery_attempt(self.path, identifier)
        self.assertEqual(attempt["id"], identifier)
        self.assertEqual(attempt["state"], "unconfirmed")
        self.assertIsNone(attempt["receipt"])
        self.assertEqual(self.path.read_bytes(), before)

    def test_H10_lookup_validates_before_opening_the_journal(self):
        with patch.object(journal, "database", side_effect=AssertionError("Database opened")):
            for identifier in (None, "A" * 32, "0" * 33, "' OR 1=1"):
                with self.subTest(identifier=identifier), self.assertRaisesRegex(journal.JournalError, "attempt ID"):
                    journal.delivery_attempt(self.path, identifier)
        self.assertFalse(self.path.exists())

    def test_H11_receipt_cli_distinguishes_found_missing_and_invalid_ids(self):
        self.seed(2)
        identifier = "%032x" % 1
        journal.finish_attempt(self.path, identifier, {"status": "delivery-unknown", "attempts": 1, "http_status": None})
        before = self.path.read_bytes()
        cases = ((identifier, 0), ("f" * 32, 1), ("private-input!", 2))
        for value, expected_exit in cases:
            argv = ["phoenix", "delivery-receipt", "--journal", str(self.path), "--attempt-id", value]
            stdout, stderr = io.StringIO(), io.StringIO()
            with self.subTest(value=value), patch("sys.argv", argv), contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                self.assertEqual(cli.main(), expected_exit)
            if expected_exit == 0:
                result = json.loads(stdout.getvalue())
                self.assertEqual(result["kind"], "local-delivery-receipt")
                self.assertEqual(result["attempt"]["id"], identifier)
                self.assertEqual(result["attempt"]["receipt"]["status"], "delivery-unknown")
            elif expected_exit == 1:
                self.assertIsNone(json.loads(stdout.getvalue())["attempt"])
            else:
                self.assertEqual(stdout.getvalue(), "")
                self.assertNotIn(value, stderr.getvalue())
        self.assertEqual(self.path.read_bytes(), before)

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.path = Path(self.directory.name) / "attempts.sqlite3"

    def tearDown(self):
        self.directory.cleanup()

    def seed(self, count, tied=False):
        metadata = {"started_at": "2026-11-01T00:00:00+00:00", "event_id": "synthetic",
                    "source_sha256": "a" * 64, "destination_sha256": "b" * 64, "worker_sha256": "c" * 64}
        journal.start_attempt(self.path, metadata)
        with journal.database(self.path) as connection, connection:
            connection.execute("DELETE FROM delivery_attempts")
            for n in range(1, count + 1):
                timestamp = datetime(2026, 11, 1, tzinfo=timezone.utc) + timedelta(seconds=0 if tied else n)
                connection.execute("INSERT INTO delivery_attempts VALUES (?, ?, ?, ?, ?, ?, NULL)",
                                   ("%032x" % n, timestamp.isoformat(), "synthetic-%s" % n,
                                    "a" * 64, "b" * 64, "c" * 64))

    def walk(self, limit, cursor=None):
        rows = []
        for _ in range(150):
            page = journal.delivery_history_page(self.path, limit, cursor)
            rows.extend(page["attempts"])
            cursor = page["next_cursor"]
            if cursor is None:
                return rows
        self.fail("Pagination did not terminate")

    def test_H01_traverses_more_than_one_hundred_rows_without_mutation(self):
        self.seed(123)
        before = self.path.read_bytes()
        rows = self.walk(17)
        self.assertEqual([r["id"] for r in rows], ["%032x" % n for n in range(123, 0, -1)])
        self.assertTrue(all(r["state"] == "unconfirmed" and r["receipt"] is None for r in rows))
        self.assertEqual(self.path.read_bytes(), before)

    def test_H02_equal_timestamps_use_the_id_tie_breaker(self):
        self.seed(7, tied=True)
        self.assertEqual([r["id"] for r in self.walk(3)], ["%032x" % n for n in range(7, 0, -1)])

    def test_H03_newer_insert_does_not_shift_the_continuation(self):
        self.seed(7)
        first = journal.delivery_history_page(self.path, 3)
        inserted = journal.start_attempt(self.path, {"started_at": "2026-11-02T00:00:00+00:00", "event_id": "newer",
                                "source_sha256": "a" * 64, "destination_sha256": "b" * 64, "worker_sha256": "c" * 64})
        remainder = self.walk(2, first["next_cursor"])
        self.assertEqual([r["id"] for r in remainder], ["%032x" % n for n in [4, 3, 2, 1]])
        self.assertEqual(journal.delivery_history_page(self.path, 1)["attempts"][0]["id"], inserted)

    def test_H04_finalization_can_update_receipts_without_changing_order(self):
        self.seed(7)
        first = journal.delivery_history_page(self.path, 3)
        journal.finish_attempt(self.path, "%032x" % 4, {"status": "delivery-unknown", "attempts": 1, "http_status": None})
        rows = self.walk(3, first["next_cursor"])
        self.assertEqual([r["id"] for r in rows], ["%032x" % n for n in [4, 3, 2, 1]])
        self.assertEqual((rows[0]["state"], rows[0]["receipt"]["status"]), ("finished", "delivery-unknown"))

    def test_H05_invalid_cursors_refuse_before_opening_a_missing_journal(self):
        def encoded(value):
            return base64.urlsafe_b64encode(json.dumps(value, separators=(",", ":")).encode()).decode().rstrip("=")
        invalid = ["", True, [], "!private-input!", "a" * 257, "a", "W10=", encoded({}),
                   encoded([True, "date", "a" * 32]), encoded([2, "date", "a" * 32]),
                   encoded([1, "", "a" * 32]), encoded([1, "x" * 65, "a" * 32]),
                   encoded([1, "\n", "a" * 32]), encoded([1, "date", "a" * 31]),
                   encoded([1, "date", "A" * 32]), encoded([1, "date", "a" * 32, "extra"])]
        position = [1, "dates", "a" * 32]
        raw = json.dumps(position, separators=(",", ":")).encode()
        spaced = base64.urlsafe_b64encode(json.dumps(position).encode()).decode().rstrip("=")
        escaped = base64.urlsafe_b64encode(raw.replace(b"a", b"\\u0061", 1)).decode().rstrip("=")
        canonical = base64.urlsafe_b64encode(raw).decode().rstrip("=")
        alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_"
        nonzero_pad_bits = canonical[:-1] + alphabet[alphabet.index(canonical[-1]) + 1]
        self.assertEqual(base64.urlsafe_b64decode(nonzero_pad_bits + "=="), raw)
        invalid.extend([spaced, escaped, nonzero_pad_bits])
        for cursor in invalid:
            with self.subTest(cursor=cursor), patch.object(journal, "database", side_effect=AssertionError("Database accessed before cursor refusal")), \
                    self.assertRaisesRegex(journal.JournalError, "cursor"):
                journal.delivery_history_page(self.path, 20, cursor)
        self.assertFalse(self.path.exists())

    def test_H06_empty_and_exact_terminal_pages_have_no_cursor(self):
        self.seed(0)
        self.assertEqual(journal.delivery_history_page(self.path), {"attempts": [], "next_cursor": None})
        self.seed(2)
        self.assertIsNone(journal.delivery_history_page(self.path, 2)["next_cursor"])
        for limit in [0, 101, True, "1"]:
            with self.subTest(limit=limit), self.assertRaises(journal.JournalError):
                journal.delivery_history_page(self.path, limit)
        first = journal.delivery_history_page(self.path, 1)
        self.assertIsInstance(first["next_cursor"], str)
        last = journal.delivery_history_page(self.path, 1, first["next_cursor"])
        self.assertEqual([r["id"] for r in last["attempts"]], ["%032x" % 1])
        self.assertIsNone(last["next_cursor"])

    def test_H07_cli_continues_and_redacts_invalid_cursor_diagnostics(self):
        self.seed(3)
        before = self.path.read_bytes()
        def run(extra):
            output, errors = io.StringIO(), io.StringIO()
            with patch("sys.argv", ["phoenix", "delivery-history", "--journal", str(self.path), "--limit", "2"] + extra), \
                    contextlib.redirect_stdout(output), contextlib.redirect_stderr(errors):
                code = cli.main()
            return code, output.getvalue(), errors.getvalue()
        code, output, _ = run([])
        self.assertEqual(code, 0)
        first = json.loads(output)
        code, output, _ = run(["--cursor", first["next_cursor"]])
        self.assertEqual(code, 0)
        self.assertEqual([r["id"] for r in json.loads(output)["attempts"]], ["%032x" % 1])
        code, output, errors = run(["--cursor", "!synthetic-private-cursor!"])
        self.assertEqual(code, 2)
        self.assertNotIn("synthetic-private-cursor", output + errors)
        self.assertEqual(self.path.read_bytes(), before)

    def test_H08_legacy_list_api_and_unsigned_seek_positions_remain_read_only(self):
        self.seed(30)
        rows = journal.delivery_history(self.path)
        self.assertIsInstance(rows, list)
        self.assertEqual([r["id"] for r in rows], ["%032x" % n for n in range(30, 10, -1)])
        # Unsigned positions are not credentials; even SQL-like text stays a bound value.
        raw = json.dumps([1, "z' OR 1=1 --", "0" * 32], separators=(",", ":")).encode()
        cursor = base64.urlsafe_b64encode(raw).decode().rstrip("=")
        before = self.path.read_bytes()
        self.assertEqual(len(journal.delivery_history_page(self.path, 100, cursor)["attempts"]), 30)
        self.assertEqual(self.path.read_bytes(), before)

    def test_H12_state_filter_applies_before_the_page_limit(self):
        self.seed(125, tied=True)
        receipt = json.dumps({"status": "delivery-unknown", "attempts": 1, "http_status": None})
        with journal.database(self.path) as connection, connection:
            connection.execute("UPDATE delivery_attempts SET receipt_json = ? WHERE id > ?", (receipt, "%032x" % 3))
        before = self.path.read_bytes()
        first = journal.delivery_history_page(self.path, 2, state="unconfirmed")
        self.assertEqual([row["id"] for row in first["attempts"]], ["%032x" % n for n in (3, 2)])
        self.assertIsNotNone(first["next_cursor"])
        last = journal.delivery_history_page(self.path, 2, first["next_cursor"], state="unconfirmed")
        self.assertEqual([row["id"] for row in last["attempts"]], ["%032x" % 1])
        self.assertIsNone(last["next_cursor"])
        finished = journal.delivery_history_page(self.path, 2, state="finished")
        self.assertEqual([row["id"] for row in finished["attempts"]], ["%032x" % n for n in (125, 124)])
        self.assertTrue(all(row["receipt"]["status"] == "delivery-unknown" for row in finished["attempts"]))
        self.assertEqual(self.path.read_bytes(), before)

    def test_H13_cli_state_filter_observes_finalization_between_pages(self):
        self.seed(3)

        def page(extra):
            argv = ["phoenix", "delivery-history", "--journal", str(self.path), "--limit", "1",
                    "--state", "unconfirmed", *extra]
            output = io.StringIO()
            with patch("sys.argv", argv), contextlib.redirect_stdout(output):
                self.assertEqual(cli.main(), 0)
            return json.loads(output.getvalue())

        first = page([])
        self.assertEqual(first["attempts"][0]["id"], "%032x" % 3)
        journal.finish_attempt(self.path, "%032x" % 2, {"status": "delivered", "attempts": 1, "http_status": 202})
        last = page(["--cursor", first["next_cursor"]])
        self.assertEqual([row["id"] for row in last["attempts"]], ["%032x" % 1])
        self.assertIsNone(last["next_cursor"])

    def test_H14_unknown_state_refuses_before_journal_access(self):
        with patch.object(journal, "database", side_effect=AssertionError("Database opened")):
            for state in ("", "delivered", "unknown", True):
                with self.subTest(state=state), self.assertRaisesRegex(journal.JournalError, "History state"):
                    journal.delivery_history_page(self.path, state=state)
        self.assertFalse(self.path.exists())

    def test_H15_event_filter_finds_sparse_exact_matches(self):
        self.seed(125, tied=True)
        event_id = "evt-é' OR 1=1 --"
        with journal.database(self.path) as connection, connection:
            connection.execute("UPDATE delivery_attempts SET event_id = ? WHERE id <= ?", (event_id, "%032x" % 3))
        before = self.path.read_bytes()
        first = journal.delivery_history_page(self.path, 2, event_id=event_id)
        self.assertEqual([row["id"] for row in first["attempts"]], ["%032x" % n for n in (3, 2)])
        self.assertIsNotNone(first["next_cursor"])
        last = journal.delivery_history_page(self.path, 2, first["next_cursor"], event_id=event_id)
        self.assertEqual([row["id"] for row in last["attempts"]], ["%032x" % 1])
        self.assertIsNone(last["next_cursor"])
        self.assertEqual(journal.delivery_history_page(self.path, event_id=event_id.upper()),
                         {"attempts": [], "next_cursor": None})
        self.assertEqual(self.path.read_bytes(), before)

    def test_H16_cli_combines_event_and_state_across_pages(self):
        self.seed(6, tied=True)
        event_id = "synthetic-match"
        with journal.database(self.path) as connection, connection:
            connection.execute("UPDATE delivery_attempts SET event_id = ? WHERE id <= ?", (event_id, "%032x" % 4))
        journal.finish_attempt(self.path, "%032x" % 2, {"status": "delivery-unknown", "attempts": 1})
        journal.finish_attempt(self.path, "%032x" % 6, {"status": "delivered", "attempts": 1})
        before = self.path.read_bytes()

        def page(state, extra=()):
            argv = ["phoenix", "delivery-history", "--journal", str(self.path), "--limit", "2",
                    "--event-id", event_id, "--state", state, *extra]
            output = io.StringIO()
            with patch("sys.argv", argv), contextlib.redirect_stdout(output):
                self.assertEqual(cli.main(), 0)
            return json.loads(output.getvalue())

        first = page("unconfirmed")
        self.assertEqual([row["id"] for row in first["attempts"]], ["%032x" % n for n in (4, 3)])
        self.assertIsNotNone(first["next_cursor"])
        last = page("unconfirmed", ("--cursor", first["next_cursor"]))
        self.assertEqual([row["id"] for row in last["attempts"]], ["%032x" % 1])
        self.assertIsNone(last["next_cursor"])
        finished = page("finished")
        self.assertEqual([row["id"] for row in finished["attempts"]], ["%032x" % 2])
        self.assertEqual(finished["attempts"][0]["receipt"]["status"], "delivery-unknown")
        self.assertIsNone(finished["next_cursor"])
        self.assertEqual(self.path.read_bytes(), before)

    def test_H17_invalid_event_ids_refuse_before_journal_access(self):
        with patch.object(journal, "database", side_effect=AssertionError("Database opened")):
            for event_id in ("", 1, "a" * 201, "\x00", "\x7f", "雪"):
                with self.subTest(event_id=event_id), self.assertRaisesRegex(journal.JournalError, "event ID"):
                    journal.delivery_history_page(self.path, event_id=event_id)
            value = "synthetic-private\ninput"
            argv = ["phoenix", "delivery-history", "--journal", str(self.path), "--event-id", value]
            stdout, stderr = io.StringIO(), io.StringIO()
            with patch("sys.argv", argv), contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                self.assertEqual(cli.main(), 2)
            self.assertEqual(stdout.getvalue(), "")
            self.assertNotIn("synthetic-private", stderr.getvalue())
        self.assertFalse(self.path.exists())

    def test_H18_destination_filter_combines_exact_match_state_and_cursor(self):
        self.seed(125, tied=True)
        destination = "http://127.0.0.1:9000/webhook?key=synthetic"
        other = "http://127.0.0.1:9000/webhook?key=other"
        event_id = "synthetic-shared"
        expected_hash = hashlib.sha256(destination.encode("ascii")).hexdigest()
        other_hash = hashlib.sha256(other.encode("ascii")).hexdigest()
        with journal.database(self.path) as connection, connection:
            connection.execute("UPDATE delivery_attempts SET event_id = ?, destination_sha256 = ?",
                               (event_id, other_hash))
            connection.execute("UPDATE delivery_attempts SET destination_sha256 = ? WHERE id <= ?",
                               (expected_hash, "%032x" % 5))
            connection.execute("UPDATE delivery_attempts SET event_id = ? WHERE id = ?",
                               ("different-event", "%032x" % 5))
        journal.finish_attempt(self.path, "%032x" % 4, {"status": "delivered", "attempts": 1})
        before = self.path.read_bytes()

        def page(url, extra=()):
            argv = ["phoenix", "delivery-history", "--journal", str(self.path), "--limit", "2",
                    "--destination", url, "--state", "unconfirmed", "--event-id", event_id, *extra]
            output = io.StringIO()
            with patch("sys.argv", argv), contextlib.redirect_stdout(output):
                self.assertEqual(cli.main(), 0)
            self.assertNotIn(destination, output.getvalue())
            return json.loads(output.getvalue())

        first = page(destination)
        self.assertEqual([row["id"] for row in first["attempts"]], ["%032x" % n for n in (3, 2)])
        self.assertIsNotNone(first["next_cursor"])
        last = page(destination, ("--cursor", first["next_cursor"]))
        self.assertEqual([row["id"] for row in last["attempts"]], ["%032x" % 1])
        self.assertIsNone(last["next_cursor"])
        self.assertEqual(page(destination.split("?")[0])["attempts"], [])
        self.assertEqual(page(other)["attempts"][0]["id"], "%032x" % 125)
        self.assertEqual(self.path.read_bytes(), before)

    def test_H19_invalid_destination_refuses_without_journal_access_or_url_echo(self):
        with patch.object(journal, "database", side_effect=AssertionError("Database opened")):
            for url in ("", 7, "http://localhost:9000/", "http://127.0.0.1/", "http://127.0.0.1:9000/#frag"):
                with self.subTest(url=url), self.assertRaisesRegex(journal.JournalError, "destination"):
                    journal.delivery_history_page(self.path, destination=url)
            marker = "synthetic-private"
            url = "http://127.0.0.1:" + marker + "/"
            try:
                journal.delivery_history_page(self.path, destination=url)
            except journal.JournalError as error:
                self.assertNotIn(marker, "".join(traceback.format_exception(type(error), error, error.__traceback__)))
            else:
                self.fail("Malformed port was accepted")
            argv = ["phoenix", "delivery-history", "--journal", str(self.path), "--destination", url]
            stdout, stderr = io.StringIO(), io.StringIO()
            with patch("sys.argv", argv), contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                self.assertEqual(cli.main(), 2)
            self.assertEqual(stdout.getvalue(), "")
            self.assertNotIn(marker, stderr.getvalue())
        self.assertFalse(self.path.exists())
