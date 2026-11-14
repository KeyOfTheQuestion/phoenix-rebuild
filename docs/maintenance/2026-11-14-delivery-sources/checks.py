"""Independent HTTP receivers exercise the single-attempt delivery client."""
import contextlib
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import io
import json
import os
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from phoenix import __main__ as cli
from phoenix.delivery import DeliveryError, MAX_EVENT_JSON, deliver_event
from phoenix.records import ROOT
from phoenix.verify import Subject


TOKEN = "synthetic-source-admin"
BODY = " \n投递原文 🔥\n"


def event():
    return {"id": "event-1", "body": BODY, "body_sha256": hashlib.sha256(BODY.encode()).hexdigest(),
            "headers": {"content-type": "text/plain; charset=utf-8", "x-webhook-id": "delivery-key",
                        "x-event-type": "delivery.fixture"}}


class FixtureServer(ThreadingHTTPServer):
    request_queue_size = 32
    daemon_threads = True


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_GET(self):
        state = self.server.state
        state["requests"].append({"method": "GET", "headers": dict(self.headers), "path": self.path})
        if state.get("on_get"):
            state["on_get"]()
        status = state["status"]
        if self.server.source and self.headers.get("Authorization") != "Bearer " + TOKEN:
            status = 401
        raw = json.dumps(state["event"], ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        if state["location"]:
            self.send_header("Location", state["location"])
        self.end_headers()
        try:
            if state["trickle"]:
                for byte in raw:
                    self.wfile.write(bytes([byte]))
                    self.wfile.flush()
                    time.sleep(0.05)
            else:
                self.wfile.write(raw)
        except OSError:
            pass

    def do_POST(self):
        state = self.server.state
        body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
        state["requests"].append({"method": "POST", "headers": dict(self.headers), "path": self.path, "body": body})
        if state.get("on_post"):
            state["on_post"]()
        time.sleep(state["delay"])
        try:
            self.send_response(state["status"])
            self.send_header("Content-Length", str(len(state["response"])))
            if state["location"]:
                self.send_header("Location", state["location"])
            self.end_headers()
            self.wfile.write(state["response"])
        except OSError:
            pass


class DeliveryFixture:
    backend = ROOT / "examples/reference/server.py"

    def setUp(self):
        self.servers = []
        self.source, self.source_state = self.server(True)
        self.destination, self.destination_state = self.server(False)

    def server(self, source):
        state = {"requests": [], "event": event(), "status": 200 if source else 202,
                 "response": b"accepted", "location": "", "delay": 0, "trickle": False}
        server = FixtureServer(("127.0.0.1", 0), Handler)
        server.state = state
        server.source = source
        thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
        thread.start()
        self.servers.append((server, thread))
        return "http://127.0.0.1:%s" % server.server_port, state

    def tearDown(self):
        for server, thread in self.servers:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)

    def deliver(self, **kwargs):
        parameters = {"source": self.source, "event_id": "event-1", "destination": self.destination + "/receive",
                      "admin_token": TOKEN, "timeout": 3}
        parameters.update(kwargs)
        return deliver_event(**parameters)


class DeliveryTests(DeliveryFixture, unittest.TestCase):
    def test_D01_delivers_the_actual_inbox_body_and_selected_headers(self):
        with tempfile.TemporaryDirectory() as temporary:
            subject = Subject(self.backend, temporary)
            try:
                subject.start()
                status, inbox, _ = subject.request("POST", "/api/inboxes", {"name": "Synthetic delivery inbox"})
                self.assertEqual(status, 201)
                headers = {"Content-Type": "text/plain; charset=utf-8", "X-Webhook-Id": "delivery-key",
                           "X-Event-Type": "delivery.fixture", "Cookie": "synthetic-private-cookie"}
                status, received, _ = subject.request("POST", "/hooks/%s/%s" % (inbox["id"], inbox["token"]),
                                                     BODY, headers, admin=False)
                self.assertEqual(status, 201)
                identifier = received["id"]
                before = subject.request("GET", "/api/events/" + identifier)[1]
                report = self.deliver(source="http://127.0.0.1:%s" % subject.port, event_id=identifier,
                                      admin_token=subject.token, destination=self.destination + "/receive?fixture=1")
                self.assertEqual((report["status"], report["http_status"], report["attempts"]), ("delivered", 202, 1))
                self.assertEqual(report["body_sha256"], hashlib.sha256(BODY.encode()).hexdigest())
                self.assertEqual(report["request_bytes"], len(BODY.encode()))
                self.assertEqual(len(self.destination_state["requests"]), 1)
                request = self.destination_state["requests"][0]
                self.assertEqual(request["method"], "POST")
                self.assertEqual(request["path"], "/receive?fixture=1")
                self.assertEqual(request["body"], BODY.encode())
                captured = {key.lower(): value for key, value in request["headers"].items()}
                self.assertEqual(captured["x-webhook-id"], "delivery-key")
                self.assertEqual(captured["x-event-type"], "delivery.fixture")
                self.assertEqual(captured["content-type"], headers["Content-Type"])
                self.assertEqual(captured["x-phoenix-event-id"], identifier)
                self.assertNotIn("authorization", captured)
                self.assertNotIn("cookie", captured)
                self.assertNotIn(subject.token, json.dumps(report))
                self.assertEqual(subject.request("GET", "/api/events/" + identifier)[1], before)
                policy = Path(temporary) / "destination-policy.json"
                raw_policy = json.dumps({"schema_version": 1, "destinations": [self.destination + "/approved"]}).encode()
                policy.write_bytes(raw_policy)
                approved = self.deliver(source="http://127.0.0.1:%s" % subject.port, event_id=identifier,
                                        admin_token=subject.token, destination=self.destination + "/approved", policy=policy,
                                        journal=Path(temporary) / "attempts.sqlite3")
                self.assertEqual(approved["status"], "delivered")
                self.assertEqual(approved["destination_policy_sha256"], hashlib.sha256(raw_policy).hexdigest())
                self.assertEqual(self.destination_state["requests"][-1]["body"], BODY.encode())
                from phoenix.delivery_journal import delivery_attempt, delivery_history_page
                page = delivery_history_page(Path(temporary) / "attempts.sqlite3", 1,
                                             state="finished", event_id=identifier, destination=self.destination + "/approved")
                self.assertEqual(delivery_attempt(Path(temporary) / "attempts.sqlite3", approved["journal_attempt_id"]),
                                 page["attempts"][0])
                self.assertEqual(page["attempts"][0]["event_id"], identifier)
                self.assertEqual(page["attempts"][0]["receipt"]["destination_policy_sha256"], approved["destination_policy_sha256"])
                self.assertIsNone(page["next_cursor"])
            finally:
                subject.stop()

    def test_D02_redirects_are_reported_without_following_them(self):
        self.destination_state.update(status=302, location=self.destination + "/second")
        report = self.deliver()
        self.assertEqual((report["status"], report["http_status"]), ("rejected", 302))
        self.assertEqual(len(self.destination_state["requests"]), 1)
        self.destination_state["requests"].clear()
        self.source_state.update(status=302, location=self.destination + "/credential-target")
        report = self.deliver()
        self.assertEqual((report["status"], report["attempts"]), ("source-error", 0))
        self.assertEqual(self.destination_state["requests"], [])

    def test_D03_receiver_failure_is_one_attempt(self):
        self.destination_state["status"] = 503
        report = self.deliver()
        self.assertEqual((report["status"], report["http_status"], report["attempts"]), ("rejected", 503, 1))
        self.assertEqual(len(self.destination_state["requests"]), 1)

    def test_D04_invalid_source_events_never_start_delivery(self):
        bad_events = [dict(event(), id="different"), dict(event(), body_sha256="0" * 64),
                      dict(event(), body=42), dict(event(), extra="x" * MAX_EVENT_JSON),
                      dict(event(), body="a" * 65537, body_sha256=hashlib.sha256(b"a" * 65537).hexdigest()),
                      dict(event(), headers={"x-event-type": "bad\r\nAuthorization: injected"})]
        for value in bad_events:
            with self.subTest(fields=tuple(value)):
                self.source_state["event"] = value
                report = self.deliver()
                self.assertEqual((report["status"], report["attempts"]), ("source-error", 0))
        self.assertEqual(self.destination_state["requests"], [])
        self.assertEqual(self.deliver(admin_token="incorrect")["attempts"], 0)

    def test_D05_invalid_addresses_credentials_and_deadlines_refuse_before_network(self):
        for destination in ("http://localhost:80/", "http://127.1:80/", "https://127.0.0.1:80/",
                            "http://127.0.0.1:80@other/", "http://127.0.0.1:80/#fragment", "file:///tmp/unused",
                            self.destination + "/bad\r\nheader"):
            with self.subTest(destination=destination), self.assertRaises(DeliveryError):
                self.deliver(destination=destination)
        for timeout in (0, True, float("nan"), float("inf"), 11):
            with self.subTest(timeout=timeout), self.assertRaises(DeliveryError):
                self.deliver(timeout=timeout)
        with self.assertRaises(DeliveryError):
            self.deliver(source=self.source + "/api")
        with self.assertRaises(DeliveryError):
            self.deliver(admin_token="bad\r\ncredential")
        self.assertEqual(self.source_state["requests"], [])
        self.assertEqual(self.destination_state["requests"], [])

    def test_D06_source_credentials_and_unselected_headers_are_not_forwarded(self):
        self.source_state["event"]["headers"].update(
            authorization="Bearer synthetic-event-secret", cookie="synthetic-cookie", host="source.invalid",
            connection="keep-alive", **{"content-length": "999", "transfer-encoding": "chunked"})
        report = self.deliver()
        self.assertEqual(report["status"], "delivered")
        request = self.destination_state["requests"][0]
        headers = {key.lower(): value for key, value in request["headers"].items()}
        self.assertNotIn("authorization", headers)
        self.assertNotIn("cookie", headers)
        self.assertNotIn("transfer-encoding", headers)
        self.assertNotEqual(headers["host"], "source.invalid")
        self.assertEqual(headers["content-length"], str(len(BODY.encode())))
        self.assertEqual(self.source_state["requests"][0]["headers"]["Authorization"], "Bearer " + TOKEN)

    def test_D07_deadline_after_post_keeps_the_outcome_unknown(self):
        self.destination_state["delay"] = 3
        started = time.monotonic()
        report = self.deliver(timeout=0.8)
        self.assertLess(time.monotonic() - started, 1.8)
        self.assertEqual((report["status"], report["attempts"], report["http_status"]), ("delivery-unknown", 1, None))
        self.assertEqual(len(self.destination_state["requests"]), 1)

    def test_D08_slow_drip_source_is_bounded_by_the_overall_deadline(self):
        self.source_state["trickle"] = True
        started = time.monotonic()
        report = self.deliver(timeout=0.4)
        self.assertLess(time.monotonic() - started, 1.4)
        self.assertEqual((report["status"], report["attempts"]), ("source-error", 0))
        self.assertEqual(self.destination_state["requests"], [])

    def test_D09_receiver_response_capture_is_capped(self):
        self.destination_state["response"] = b"receiver-private-content" * 1000
        report = self.deliver()
        self.assertEqual(report["status"], "delivered")
        self.assertEqual(report["response_bytes_observed"], 8193)
        self.assertTrue(report["response_truncated"])
        self.assertNotIn("receiver-private-content", json.dumps(report))

    def test_D10_cli_uses_the_token_environment_and_truthful_exit_codes(self):
        def run(destination):
            output, errors = io.StringIO(), io.StringIO()
            arguments = ["phoenix", "deliver-event", "--source", self.source, "--event-id", "event-1",
                         "--destination", destination]
            with patch.object(os, "environ", dict(os.environ, PHOENIX_ADMIN_TOKEN=TOKEN)), \
                    patch("sys.argv", arguments), contextlib.redirect_stdout(output), contextlib.redirect_stderr(errors):
                code = cli.main()
            self.assertNotIn(TOKEN, output.getvalue() + errors.getvalue())
            return code
        self.assertEqual(run(self.destination), 0)
        self.destination_state["status"] = 503
        self.assertEqual(run(self.destination), 1)
        self.assertEqual(run("http://example.invalid/"), 2)

    def test_D11_empty_and_maximum_utf8_bodies_arrive_exactly(self):
        for body in ("", "\x00" * 65536):
            with self.subTest(bytes=len(body.encode())):
                self.source_state["event"] = dict(event(), body=body, body_sha256=hashlib.sha256(body.encode()).hexdigest())
                report = self.deliver()
                self.assertEqual(report["status"], "delivered")
                self.assertEqual(report["request_bytes"], len(body.encode()))
                self.assertEqual(self.destination_state["requests"][-1]["body"], body.encode())

    def test_D12_inconsistent_or_partial_worker_messages_keep_receipts_truthful(self):
        messages = [
            (b'{"result":{"status":"delivered","attempts":0,"http_status":200}}\n', 0),
            (b'{"phase":"post-started"}\n{"result":{"status":"delivered","attempts":1,"http_status":503}}\n', 1),
            (b'{"phase":"post-started"}\n{"res', 1),
        ]
        for output, attempts in messages:
            with self.subTest(output=output), patch("phoenix.delivery.subprocess.Popen") as start:
                process = start.return_value
                process.returncode = 0
                process.communicate.return_value = (output, b"")
                report = self.deliver()
                self.assertEqual(report["attempts"], attempts)
                self.assertEqual(report["status"], "delivery-unknown" if attempts else "source-error")
                self.assertIsNone(report["http_status"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
