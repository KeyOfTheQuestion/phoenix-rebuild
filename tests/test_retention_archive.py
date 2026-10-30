"""Frozen reference archives are not AI attempts; every test uses a disposable ledger."""
import contextlib
import hashlib
import io
import json
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch

from phoenix import __main__ as cli
from phoenix.audit import audit, verified_run
from phoenix.records import ROOT, publish
from phoenix.retention_archive import publish_retention


class RetentionArchiveTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fixture = tempfile.TemporaryDirectory(prefix="phoenix-retention-archive-fixture-")
        cls.addClassCleanup(cls.fixture.cleanup)
        cls.fixture_root = Path(cls.fixture.name)
        with patch("phoenix.records.ROOT", cls.fixture_root):
            cls.report = publish_retention(ROOT / "examples/retention/server.py", ROOT / "examples/reference/server.py")
        cls.row = json.loads((cls.fixture_root / "docs/runs.json").read_text())[0]
        assert audit(cls.fixture_root / "docs") == []

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="phoenix-retention-archive-test-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.site = self.root / "docs"
        shutil.copytree(self.fixture_root / "docs", self.site)
        self.directory = self.site / "evidence" / self.row["id"]

    def replay(self, container=False):
        output, errors = io.StringIO(), io.StringIO()
        with patch.object(cli, "ROOT", self.root), patch.object(sys, "argv",
                ["phoenix", "replay", self.row["id"]] + (["--container"] if container else [])), \
                contextlib.redirect_stdout(output), contextlib.redirect_stderr(errors):
            result = cli.main()
        return result, output.getvalue(), errors.getvalue()

    def write_report(self, report):
        path = self.directory / "report.json"
        path.write_text(json.dumps(report))
        manifest_path = self.directory / "manifest.json"
        manifest = json.loads(manifest_path.read_text())
        manifest["report.json"] = hashlib.sha256(path.read_bytes()).hexdigest()
        manifest_path.write_text(json.dumps(manifest))
        (self.site / "runs.json").write_text(json.dumps([{key: report.get(key) for key in self.row}]))

    def test_published_reference_has_complete_frozen_dependencies_and_32_check_profile(self):
        report, artifacts = verified_run(self.site, self.row["id"])
        self.assertEqual((report["contract_id"], report["contract_version"], report["kind"]),
                         ("002", "2.0.0", "retention-reference-verification"))
        self.assertEqual((report["passed"], report["total"]), (32, 32))
        self.assertEqual(set(artifacts), {"server.py", "verifier.py", "predecessor.py", "contract.md", "report.json",
                                         "baseline.py", "retention.py", "records.py", "audit.py"})
        self.assertIsNone(report["usage"])
        self.assertIsNone(report["cost_usd"])
        self.assertEqual(report["provenance"]["generator"], "reference-verification")

    def test_replay_uses_archived_modules_not_live_code_and_leaves_archive_unchanged(self):
        before = {p.name: p.read_bytes() for p in self.directory.iterdir()}
        with patch("phoenix.retention.evaluate_retention_full", side_effect=AssertionError("live code must not execute")):
            result, output, errors = self.replay()
        self.assertEqual(result, 0, errors)
        report = json.loads(output)
        self.assertEqual((report["passed"], report["total"]), (32, 32))
        self.assertEqual(report["replay_source"], "verified-archive-bytes")
        self.assertEqual(report["verifier_sha256"], self.report["verifier_sha256"])
        self.assertEqual(before, {p.name: p.read_bytes() for p in self.directory.iterdir()})
        self.assertFalse(any(name.startswith("_phoenix_retention_archive_") for name in sys.modules))

    def test_changed_dependency_is_refused_before_any_archived_code_executes(self):
        (self.directory / "baseline.py").write_text("raise RuntimeError('tampered helper')\n")
        with patch("phoenix.retention_archive.replay_frozen_retention") as run:
            result, _, errors = self.replay()
        self.assertEqual(result, 2)
        self.assertIn("digest mismatch", errors)
        run.assert_not_called()

    def test_dependency_binding_still_matters_after_manifest_is_rehashed(self):
        helper = self.directory / "baseline.py"
        helper.write_bytes(helper.read_bytes() + b"\n# changed helper\n")
        path = self.directory / "manifest.json"
        manifest = json.loads(path.read_text())
        manifest["baseline.py"] = hashlib.sha256(helper.read_bytes()).hexdigest()
        path.write_text(json.dumps(manifest))
        self.assertTrue(any("Report digest mismatch" in error for error in audit(self.site)))

    def test_replay_keeps_checked_bytes_when_archive_files_change_after_validation(self):
        def check_then_change(site, run_id):
            checked = verified_run(site, run_id)
            for name in ("baseline.py", "retention.py", "server.py"):
                (self.directory / name).write_text("raise RuntimeError('reopened an unchecked path')\n")
            return checked
        with patch("phoenix.audit.verified_run", side_effect=check_then_change):
            result, output, errors = self.replay()
        self.assertEqual(result, 0, errors)
        self.assertEqual(json.loads(output)["passed"], 32)

    def test_missing_container_runtime_refuses_before_loading_a_consistent_host_marker(self):
        marker = self.root / "must-not-load.txt"
        entrypoint = self.directory / "verifier.py"
        entrypoint.write_text("from pathlib import Path\nPath(%r).write_text('executed')\n" % str(marker)
                              + entrypoint.read_text())
        manifest_path = self.directory / "manifest.json"
        manifest = json.loads(manifest_path.read_text())
        manifest["verifier.py"] = hashlib.sha256(entrypoint.read_bytes()).hexdigest()
        manifest_path.write_text(json.dumps(manifest))
        report = dict(self.report, verifier_sha256=manifest["verifier.py"])
        self.write_report(report)
        verified_run(self.site, self.row["id"])
        with patch("phoenix.retention_archive.replay_frozen_retention") as run, \
                patch("phoenix.container.shutil.which", return_value=None):
            result, output, errors = self.replay(container=True)
        self.assertEqual(result, 2)
        self.assertEqual(output, "")
        self.assertIn("Docker is not installed", errors)
        self.assertFalse(marker.exists())
        run.assert_not_called()

    def test_profile_refuses_ai_labels_unknown_contracts_and_incomplete_passing_checks(self):
        for changes in ({"kind": "ai-rebuild"}, {"contract_id": "999"}, {"expected_total": 31},
                        {"checks": self.report["checks"][:-1], "passed": 31, "total": 31}):
            with self.subTest(changes=tuple(changes)):
                self.write_report(dict(self.report, **changes))
                self.assertTrue(audit(self.site))

    def test_cli_refuses_publishing_the_focused_13_check_mode(self):
        with patch.object(sys, "argv", ["phoenix", "verify-retention", "--publish"]), \
                patch("phoenix.retention_archive.publish_retention") as run, contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit) as error:
                cli.main()
        self.assertEqual(error.exception.code, 2)
        run.assert_not_called()

    def test_unsafe_reserved_or_nonbyte_extra_artifacts_fail_before_publication(self):
        for extra in ({"../outside.py": b"x"}, {"report.json": b"x"}, {"helper.py": "mutable source string"}):
            with self.subTest(extra=tuple(extra)), patch("phoenix.records.ROOT", self.root):
                with self.assertRaisesRegex(ValueError, "frozen bytes"):
                    publish({}, self.root / "missing.py", extra_artifacts=extra)
        self.assertEqual(len(list((self.site / "evidence").iterdir())), 1)


if __name__ == "__main__":
    unittest.main()
