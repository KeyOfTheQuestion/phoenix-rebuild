"""Runtime observations are measured per execution, not inferred from old records."""
import contextlib
import copy
import hashlib
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from phoenix import __main__ as cli
from phoenix.audit import audit
from phoenix.records import ROOT, publish
from phoenix.retention import evaluate_retention_full
from phoenix.verify import evaluate


class RuntimeEvidenceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.reference = ROOT / "examples/reference/server.py"
        cls.report = evaluate(cls.reference, predecessor=cls.reference)
        # A separate, isolated child measures the same interpreter used by Subject.
        cls.measured = json.loads(subprocess.check_output([
            sys.executable, "-I", "-c", "import json,platform,sqlite3; print(json.dumps({"
            "'python_implementation':platform.python_implementation(),"
            "'python_version':platform.python_version(),'sqlite_version':sqlite3.sqlite_version}))"
        ], text=True))

    def test_http_report_identifies_the_actual_child_runtime_without_host_paths(self):
        self.assertIn("execution_runtime", self.report)
        self.assertEqual(self.report["execution_runtime"], self.measured)
        self.assertEqual((self.report["passed"], self.report["total"]), (19, 19))
        self.assertNotIn(str(Path.home()), json.dumps(self.report["execution_runtime"]))

    def test_composed_retention_reports_the_measured_runtime_and_keeps_32_checks(self):
        report = evaluate_retention_full(ROOT / "examples/retention/server.py", self.reference)
        self.assertIn("execution_runtime", report)
        self.assertEqual(report["execution_runtime"], self.measured)
        self.assertEqual((report["passed"], report["total"]), (32, 32))

    def test_publication_retains_runtime_and_audit_rejects_malformed_observations(self):
        with tempfile.TemporaryDirectory() as temporary, patch("phoenix.records.ROOT", Path(temporary)):
            root = Path(temporary)
            report = publish(self.report, self.reference, predecessor=self.reference,
                             verifier_source=(ROOT / "phoenix/verify.py").read_bytes(),
                             contract_source=(ROOT / "specs/webhook-inbox.md").read_bytes())
            row, = json.loads((root / "docs/runs.json").read_text())
            self.assertIn("execution_runtime", row)
            self.assertEqual(row["execution_runtime"], self.measured)
            self.assertEqual(audit(root / "docs"), [])
            directory = root / "docs/evidence" / report["id"]
            for invalid in ({}, "3.9", dict(self.measured, python_version=True),
                            dict(self.measured, python_version="/private/host/python"),
                            dict(self.measured, sqlite_version=""), dict(self.measured, host="private-host")):
                with self.subTest(invalid=invalid):
                    changed = dict(report, execution_runtime=invalid)
                    path = directory / "report.json"
                    path.write_text(json.dumps(changed))
                    manifest = json.loads((directory / "manifest.json").read_text())
                    manifest["report.json"] = hashlib.sha256(path.read_bytes()).hexdigest()
                    (directory / "manifest.json").write_text(json.dumps(manifest))
                    (root / "docs/runs.json").write_text(json.dumps([dict(row, execution_runtime=invalid)]))
                    self.assertTrue(any("runtime" in e.lower() for e in audit(root / "docs")))

    def test_old_archive_replay_observes_current_runtime_without_rewriting_history(self):
        rows = json.loads((ROOT / "docs/runs.json").read_text())
        row = next(r for r in rows if r["kind"] == "ai-rebuild" and r["status"] == "passed"
                   and not r.get("execution_runtime"))
        directory = ROOT / "docs/evidence" / row["id"]
        before = {p.name: p.read_bytes() for p in directory.iterdir() if p.is_file()}
        output = io.StringIO()
        with patch.object(sys, "argv", ["phoenix", "replay", row["id"]]), contextlib.redirect_stdout(output):
            code = cli.main()
        result = json.loads(output.getvalue())
        self.assertIn(code, (0, 1))  # acceptance can differ on another Python version
        self.assertIn("execution_runtime", result)
        self.assertEqual(result["execution_runtime"], self.measured)
        self.assertEqual(before, {p.name: p.read_bytes() for p in directory.iterdir() if p.is_file()})

    def test_legacy_report_without_runtime_still_audits(self):
        with tempfile.TemporaryDirectory() as temporary, patch("phoenix.records.ROOT", Path(temporary)):
            legacy = copy.deepcopy(self.report)
            legacy.pop("execution_runtime", None)
            publish(legacy, self.reference, predecessor=self.reference,
                    verifier_source=(ROOT / "phoenix/verify.py").read_bytes(),
                    contract_source=(ROOT / "specs/webhook-inbox.md").read_bytes())
            self.assertEqual(audit(Path(temporary) / "docs"), [])


if __name__ == "__main__":
    unittest.main()
