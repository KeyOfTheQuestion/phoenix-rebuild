"""Matrix controls use archived bytes and temporary synthetic runtimes; no model."""
import contextlib
import copy
import io
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from phoenix import __main__ as cli
from phoenix.audit import verified_run
from phoenix.records import ROOT


@unittest.skipUnless(os.name == "posix", "Process-group cleanup requires POSIX")
class RuntimeMatrixTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="phoenix-matrix-test-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        rows = json.loads((ROOT / "docs/runs.json").read_text())
        self.row = next(r for r in rows if r["id"] == "20261102T020351Z-0ba772")
        self.retention = next(r for r in rows if r.get("contract_id") == "002")
        self.legacy = next(r for r in rows if r["kind"] == "ai-rebuild" and r["status"] == "passed"
                           and not r.get("predecessor_sha256"))
        for row in (self.row, self.retention, self.legacy):
            shutil.copytree(ROOT / "docs/evidence" / row["id"], self.root / "docs/evidence" / row["id"])
        (self.root / "docs/runs.json").write_text(json.dumps([self.row, self.retention, self.legacy]))
        self.original, self.artifacts = verified_run(self.root / "docs", self.row["id"])

    def invoke(self, interpreters=None, timeout=15, row=None):
        output, errors = io.StringIO(), io.StringIO()
        args = ["phoenix", "replay-matrix", (row or self.row)["id"], "--timeout", str(timeout)]
        for interpreter in interpreters or [sys.executable]:
            args += ["--python", str(interpreter)]
        with patch.object(cli, "ROOT", self.root), patch.object(sys, "argv", args), \
                contextlib.redirect_stdout(output), contextlib.redirect_stderr(errors):
            try:
                code = cli.main()
            except SystemExit as error:
                code = error.code
        return code, output.getvalue(), errors.getvalue()

    def fake_python(self, body):
        executable = self.root / "synthetic-python"
        executable.write_text("#!" + sys.executable + "\n" + body)
        executable.chmod(0o755)
        return executable

    def test_real_archived_candidate_uses_frozen_bytes_and_keeps_public_evidence_unchanged(self):
        directory = self.root / "docs/evidence" / self.row["id"]
        before = {p.name: p.read_bytes() for p in directory.iterdir()}
        ledger = (self.root / "docs/runs.json").read_bytes()
        code, output, errors = self.invoke()
        self.assertEqual(code, 0, errors)
        matrix = json.loads(output)
        self.assertEqual((matrix["status"], matrix["passed"], matrix["total"]), ("passed", 1, 1))
        self.assertEqual(matrix["kind"], "archive-runtime-matrix")
        self.assertEqual(matrix["source_run_id"], self.row["id"])
        self.assertEqual(matrix["results"][0]["report"]["passed"], 19)
        self.assertEqual(matrix["results"][0]["report"]["candidate_sha256"], self.original["candidate_sha256"])
        self.assertEqual(matrix["results"][0]["report"]["execution_runtime"]["python_version"],
                         ".".join(map(str, sys.version_info[:3])))
        self.assertEqual(before, {p.name: p.read_bytes() for p in directory.iterdir()})
        self.assertEqual(ledger, (self.root / "docs/runs.json").read_bytes())
        self.assertNotIn(str(self.root), output)

    def test_contract_002_and_early_sql_fixture_keep_their_frozen_profiles(self):
        for row, count in ((self.retention, 32), (self.legacy, 19)):
            with self.subTest(record=row["id"]):
                code, output, errors = self.invoke(row=row)
                self.assertEqual(code, 0, errors)
                self.assertEqual(json.loads(output)["results"][0]["report"]["passed"], count)

    def test_source_changes_after_audit_cannot_replace_matrix_inputs(self):
        def checked_then_changed(site, run_id):
            checked = verified_run(site, run_id)
            for name in ("server.py", "verifier.py", "predecessor.py"):
                (self.root / "docs/evidence" / run_id / name).write_text("raise RuntimeError('unchecked bytes')\n")
            return checked
        with patch("phoenix.audit.verified_run", side_effect=checked_then_changed):
            code, output, errors = self.invoke()
        self.assertEqual(code, 0, errors)
        self.assertEqual(json.loads(output)["results"][0]["report"]["candidate_sha256"], self.original["candidate_sha256"])

    def test_tampering_refuses_before_any_runtime_starts(self):
        marker = self.root / "must-not-start"
        runtime = self.fake_python("from pathlib import Path\nPath(%r).touch()\n" % str(marker))
        (self.root / "docs/evidence" / self.row["id"] / "server.py").write_bytes(b"# tampered\n")
        code, _, _ = self.invoke([runtime])
        self.assertEqual(code, 2)
        self.assertFalse(marker.exists())

    def test_relative_interpreter_symlink_preserves_the_selected_executable_path(self):
        marker = self.root / "selected-name"
        runtime = self.fake_python("import sys\nfrom pathlib import Path\nPath(%r).write_text(Path(sys.argv[0]).name)\n"
                                   "print(%r)\n" % (str(marker), json.dumps(self.original)))
        link = self.root / "selected-python"
        link.symlink_to(runtime)
        code, _, errors = self.invoke([os.path.relpath(link)])
        self.assertEqual(code, 0, errors)
        self.assertEqual(marker.read_text(), "selected-python")

    def test_acceptance_failure_is_retained_and_does_not_hide_the_other_runtime(self):
        report = copy.deepcopy(self.original)
        report["checks"][0]["passed"] = False
        report.update(passed=18, status="failed")
        runtime = self.fake_python("print(%r)\n" % json.dumps(report))
        code, output, errors = self.invoke([runtime, sys.executable])
        self.assertEqual(code, 1, errors)
        matrix = json.loads(output)
        self.assertEqual((matrix["status"], matrix["passed"], matrix["total"]), ("failed", 1, 2))
        self.assertEqual([r["status"] for r in matrix["results"]], ["failed", "passed"])

    def test_invalid_reports_and_missing_runtime_are_errors_without_fallback(self):
        for payload in (dict(self.original, candidate_sha256="0" * 64),
                        dict(self.original, checks=[], passed=0, total=0),
                        dict(self.original, expected_schema_version=True),
                        dict(self.original, execution_runtime=None)):
            with self.subTest(field=set(payload)):
                runtime = self.fake_python("print(%r)\n" % json.dumps(payload))
                code, output, errors = self.invoke([runtime, self.root / "not-installed"])
                self.assertEqual(code, 2, errors)
                results = json.loads(output)["results"]
                self.assertEqual([r["status"] for r in results], ["runtime-error", "runtime-error"])
                self.assertTrue(all("report" not in r for r in results))

    def test_runtime_count_and_deadline_are_bounded_before_execution(self):
        marker = self.root / "must-not-start"
        runtime = self.fake_python("from pathlib import Path\nPath(%r).touch()\n" % str(marker))
        for runtimes, timeout in (([runtime], 0), ([runtime], 301), ([runtime] * 5, 15)):
            with self.subTest(count=len(runtimes), timeout=timeout):
                code, _, _ = self.invoke(runtimes, timeout)
                self.assertEqual(code, 2)
                self.assertFalse(marker.exists())

    def test_timeout_terminates_the_runtime_process_group_and_continues(self):
        marker = self.root / "child-pid"
        runtime = self.fake_python("import subprocess,sys,time\nfrom pathlib import Path\n"
            "child=subprocess.Popen([sys.executable,'-c','import time; time.sleep(60)'])\n"
            "Path(%r).write_text(str(child.pid))\ntime.sleep(60)\n" % str(marker))
        code, output, errors = self.invoke([runtime, sys.executable], timeout=2)
        self.assertEqual(code, 2, errors)
        matrix = json.loads(output)
        self.assertEqual([r["status"] for r in matrix["results"]], ["runtime-error", "passed"])
        self.assertEqual(matrix["results"][0]["error"], "timeout")
        child = int(marker.read_text())
        def cleanup():
            try:
                os.kill(child, signal.SIGKILL)
            except ProcessLookupError:
                pass
        self.addCleanup(cleanup)
        state = subprocess.run(["ps", "-p", str(child), "-o", "stat="], capture_output=True, text=True).stdout.strip()
        self.assertTrue(not state or state.startswith("Z"), state)


if __name__ == "__main__":
    unittest.main()
