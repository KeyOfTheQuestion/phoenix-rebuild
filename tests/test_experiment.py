import json
from pathlib import Path
import shutil
import tempfile
import unittest

from phoenix.audit import audit
from phoenix.records import ROOT
from phoenix.verify import evaluate

REFERENCE = ROOT / "examples/reference/server.py"


class AcceptanceHarnessTests(unittest.TestCase):
    def test_reference_meets_contract_with_a_real_predecessor_database(self):
        report = evaluate(REFERENCE, predecessor=REFERENCE)
        self.assertEqual(report["status"], "passed", report)
        self.assertEqual(report["passed"], 19)
        self.assertIn("HTTP", report["compatibility_fixture"])

    def test_an_always_200_service_cannot_claim_success(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "fake.py"
            source.write_text('''import os,json
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
class Handler(BaseHTTPRequestHandler):
 def log_message(self,*args): pass
 def do_GET(self):
  body=json.dumps({"status":"ok","schema_version":1}).encode()
  self.send_response(200);self.send_header("Content-Type","application/json");self.end_headers();self.wfile.write(body)
 do_POST=do_GET
ThreadingHTTPServer(("127.0.0.1",int(os.environ["PORT"])),Handler).serve_forever()
''')
            report = evaluate(source)
            self.assertEqual(report["status"], "failed")
            self.assertFalse(next(c for c in report["checks"] if c["id"] == "S01")["passed"])

    def test_recreating_database_at_boot_fails_data_continuity(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "destructive_boot.py"
            source.write_text('''import os,sqlite3,runpy
with sqlite3.connect(os.environ["PHOENIX_DB_PATH"]) as db:
 db.execute("DROP TABLE IF EXISTS events")
 db.execute("DROP TABLE IF EXISTS inboxes")
runpy.run_path(%r,run_name="__main__")
''' % str(REFERENCE))
            report = evaluate(source, predecessor=REFERENCE)
            self.assertEqual(report["status"], "failed")
            legacy = next(c for c in report["checks"] if c["id"] == "D02")
            self.assertFalse(legacy["passed"])


class PublishedEvidenceTests(unittest.TestCase):
    def test_published_ledger_is_internally_consistent(self):
        self.assertEqual(audit(ROOT / "docs"), [])

    def test_modified_candidate_is_detected(self):
        with tempfile.TemporaryDirectory() as tmp:
            site = Path(tmp)
            run = json.loads((ROOT / "docs/runs.json").read_text())[0]
            (site / "runs.json").write_text(json.dumps([run]))
            shutil.copytree(ROOT / "docs/evidence" / run["id"], site / "evidence" / run["id"])
            (site / "evidence" / run["id"] / "server.py").write_text("# changed after publication\n")
            self.assertTrue(any("digest mismatch" in e for e in audit(site)))

    def test_ledger_cannot_change_a_verdict_without_matching_report(self):
        with tempfile.TemporaryDirectory() as tmp:
            site = Path(tmp)
            run = json.loads((ROOT / "docs/runs.json").read_text())[0]
            shutil.copytree(ROOT / "docs/evidence" / run["id"], site / "evidence" / run["id"])
            run["passed"] = 999
            (site / "runs.json").write_text(json.dumps([run]))
            self.assertTrue(any("ledger differs" in e for e in audit(site)))


if __name__ == "__main__":
    unittest.main()
