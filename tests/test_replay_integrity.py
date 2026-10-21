import contextlib
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

    def replay(self, run_id=None):
        output, errors = io.StringIO(), io.StringIO()
        with patch.object(cli, "ROOT", self.root), patch.object(
            sys, "argv", ["phoenix", "replay", run_id if run_id is not None else self.row["id"]]
        ), contextlib.redirect_stdout(output), contextlib.redirect_stderr(errors):
            result = cli.main()
        return result, output.getvalue(), errors.getvalue()

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
