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
from phoenix.audit import verified_run
from phoenix.records import ROOT


class ReplayIntegrityTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="phoenix-replay-test-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.site = self.root / "docs"
        ledger = json.loads((ROOT / "docs/runs.json").read_text())
        self.row = next(r for r in ledger if r["kind"] == "ai-rebuild" and r["status"] == "passed")
        self.directory = self.site / "evidence" / self.row["id"]
        shutil.copytree(ROOT / "docs/evidence" / self.row["id"], self.directory,
                        ignore=shutil.ignore_patterns("__pycache__"))
        (self.site / "runs.json").write_text(json.dumps([self.row]))

    def replay(self, run_id=None, container=False):
        output, errors = io.StringIO(), io.StringIO()
        with patch.object(cli, "ROOT", self.root), patch.object(
            sys, "argv", ["phoenix", "replay", run_id if run_id is not None else self.row["id"]]
            + (["--container"] if container else [])
        ), contextlib.redirect_stdout(output), contextlib.redirect_stderr(errors):
            result = cli.main()
        return result, output.getvalue(), errors.getvalue()

    def consistent_verifier_with_host_marker(self):
        # A disposable, internally consistent archive distinguishes host execution
        # from digest refusal. It never changes the published historical archive.
        marker = self.root / "archive-loaded-on-host.txt"
        verifier = self.directory / "verifier.py"
        verifier.write_text("from pathlib import Path\nPath(%r).write_text('executed')\n" % str(marker)
                            + verifier.read_text())
        report_path = self.directory / "report.json"
        report = json.loads(report_path.read_text())
        report["verifier_sha256"] = hashlib.sha256(verifier.read_bytes()).hexdigest()
        report_path.write_text(json.dumps(report))
        manifest_path = self.directory / "manifest.json"
        manifest = json.loads(manifest_path.read_text())
        manifest["verifier.py"] = hashlib.sha256(verifier.read_bytes()).hexdigest()
        manifest["report.json"] = hashlib.sha256(report_path.read_bytes()).hexdigest()
        manifest_path.write_text(json.dumps(manifest))
        verified_run(self.site, self.row["id"])
        return marker

    def test_container_replay_passes_verified_archive_bytes_without_host_execution(self):
        marker = self.consistent_verifier_with_host_marker()
        archived, artifacts = verified_run(self.site, self.row["id"])
        with patch("phoenix.container.evaluate_snapshot_in_container", return_value={"status": "passed"}) as run:
            result, output, errors = self.replay(container=True)
        self.assertEqual(result, 0, errors)
        self.assertFalse(marker.exists())
        run.assert_called_once_with(artifacts["server.py"], artifacts["verifier.py"],
                                    predecessor_source=artifacts["predecessor.py"], timeout=90)
        replay = json.loads(output)
        self.assertEqual(replay["replay_run_id"], self.row["id"])
        self.assertEqual(replay["replay_source"], "verified-archive-bytes")
        self.assertEqual(replay["contract_sha256"], archived["contract_sha256"])

    def test_missing_container_runtime_never_loads_the_archived_verifier_on_host(self):
        marker = self.consistent_verifier_with_host_marker()
        with patch("phoenix.container.shutil.which", return_value=None):
            result, output, errors = self.replay(container=True)
        self.assertEqual(result, 2)
        self.assertEqual(output, "")
        self.assertIn("Docker is not installed", errors)
        self.assertFalse(marker.exists())

    def test_container_replay_refuses_modified_evidence_before_calling_the_runtime(self):
        (self.directory / "server.py").write_text("# changed after publication\n")
        with patch("phoenix.container.evaluate_snapshot_in_container") as run:
            result, _, errors = self.replay(container=True)
        self.assertEqual(result, 2)
        self.assertIn("digest mismatch", errors)
        run.assert_not_called()

    def test_container_replay_retains_the_early_sql_fixture_verifier_without_a_predecessor(self):
        ledger = json.loads((ROOT / "docs/runs.json").read_text())
        row = next(r for r in ledger if r["kind"] == "ai-rebuild" and r["status"] == "passed"
                   and not r.get("predecessor_sha256"))
        directory = self.site / "evidence" / row["id"]
        shutil.copytree(ROOT / "docs/evidence" / row["id"], directory,
                        ignore=shutil.ignore_patterns("__pycache__"))
        (self.site / "runs.json").write_text(json.dumps([self.row, row]))
        with patch("phoenix.container.evaluate_snapshot_in_container", return_value={"status": "passed"}) as run:
            result, _, errors = self.replay(row["id"], container=True)
        self.assertEqual(result, 0, errors)
        self.assertEqual(run.call_args.args[1], (directory / "verifier.py").read_bytes())
        self.assertNotEqual(run.call_args.args[1], (ROOT / "phoenix/verify.py").read_bytes())
        self.assertIsNone(run.call_args.kwargs["predecessor_source"])

    def test_container_replay_keeps_verified_bytes_when_disk_files_change_after_validation(self):
        _, artifacts = verified_run(self.site, self.row["id"])

        def inspect_then_change(site, run_id):
            result = verified_run(site, run_id)
            (self.directory / "verifier.py").write_text("raise RuntimeError('changed after audit')\n")
            (self.directory / "server.py").write_text("raise RuntimeError('changed after audit')\n")
            return result

        with patch("phoenix.audit.verified_run", side_effect=inspect_then_change), \
                patch("phoenix.container.evaluate_snapshot_in_container", return_value={"status": "passed"}) as run:
            result, _, errors = self.replay(container=True)
        self.assertEqual(result, 0, errors)
        self.assertEqual(run.call_args.args[0], artifacts["server.py"])
        self.assertEqual(run.call_args.args[1], artifacts["verifier.py"])

    def test_valid_archive_replays_without_writing_into_evidence(self):
        before = {p.name: p.read_bytes() for p in self.directory.iterdir()}
        result, output, errors = self.replay()
        self.assertEqual(result, 0, errors)
        self.assertEqual(json.loads(output)["passed"], 19)
        self.assertEqual(before, {p.name: p.read_bytes() for p in self.directory.iterdir()})

    def test_modified_verifier_is_refused_before_execution(self):
        marker = self.root / "unverified-verifier-ran.txt"
        verifier = self.directory / "verifier.py"
        verifier.write_text("from pathlib import Path\nPath(%r).write_text('executed')\n" % str(marker)
                            + verifier.read_text())
        result, output, errors = self.replay()
        self.assertEqual(result, 2)
        self.assertFalse(marker.exists())
        self.assertEqual(output, "")
        self.assertIn("digest mismatch: verifier.py", errors)

    def test_modified_candidate_is_refused_before_startup(self):
        marker = self.root / "unverified-candidate-ran.txt"
        candidate = self.directory / "server.py"
        candidate.write_text("from pathlib import Path\nPath(%r).write_text('executed')\n" % str(marker)
                             + candidate.read_text())
        result, _, errors = self.replay()
        self.assertEqual(result, 2)
        self.assertFalse(marker.exists())
        self.assertIn("digest mismatch: server.py", errors)

    def test_manifest_cannot_omit_a_verifier_that_replay_would_load(self):
        manifest = self.directory / "manifest.json"
        entries = json.loads(manifest.read_text())
        del entries["verifier.py"]
        manifest.write_text(json.dumps(entries))
        result, _, errors = self.replay()
        self.assertEqual(result, 2)
        self.assertIn("Unlisted or missing", errors)

    def test_extra_unlisted_file_is_refused(self):
        (self.directory / "extra.py").write_text("raise RuntimeError('not recorded')\n")
        result, _, errors = self.replay()
        self.assertEqual(result, 2)
        self.assertIn("Unlisted or missing", errors)

    def test_symlinked_artifact_is_refused_even_when_bytes_match(self):
        candidate = self.directory / "server.py"
        outside = self.root / "outside-candidate.py"
        outside.write_bytes(candidate.read_bytes())
        candidate.unlink()
        try:
            candidate.symlink_to(outside)
        except OSError as error:
            self.skipTest("This platform cannot create symlinks: " + str(error))
        result, _, errors = self.replay()
        self.assertEqual(result, 2)
        self.assertIn("symlink", errors)

    def test_unsafe_and_unlisted_run_ids_are_refused(self):
        for run_id in (".", "..", "../outside", "outside\\source", "not-in-the-ledger"):
            with self.subTest(run_id=run_id):
                result, output, errors = self.replay(run_id)
                self.assertEqual(result, 2)
                self.assertEqual(output, "")
                self.assertIn("Replay refused", errors)

    def test_malformed_ledger_produces_a_controlled_error(self):
        (self.site / "runs.json").write_text("{}")
        result, _, errors = self.replay()
        self.assertEqual(result, 2)
        self.assertIn("ledger must be a list", errors)

    def test_generation_failure_is_not_treated_as_executable_source(self):
        ledger = json.loads((ROOT / "docs/runs.json").read_text())
        row = next(r for r in ledger if r["status"] == "generation-failed")
        shutil.copytree(ROOT / "docs/evidence" / row["id"], self.site / "evidence" / row["id"],
                        ignore=shutil.ignore_patterns("__pycache__"))
        (self.site / "runs.json").write_text(json.dumps([self.row, row]))
        result, _, errors = self.replay(row["id"])
        self.assertEqual(result, 2)
        self.assertIn("no executable candidate", errors)

    def test_replay_uses_verified_snapshot_if_disk_files_change_after_inspection(self):
        def inspect_then_change(site, run_id):
            result = verified_run(site, run_id)
            (self.directory / "verifier.py").write_text("raise RuntimeError('changed after audit')\n")
            (self.directory / "server.py").write_text("raise RuntimeError('changed after audit')\n")
            return result

        with patch("phoenix.audit.verified_run", side_effect=inspect_then_change):
            result, output, errors = self.replay()
        self.assertEqual(result, 0, errors)
        self.assertEqual(json.loads(output)["status"], "passed")


if __name__ == "__main__":
    unittest.main()
