"""Real HTTP controls for separately archived v2-to-v2 continuity observations."""
import contextlib
import hashlib
import importlib
import importlib.util
import io
import json
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch

from phoenix import __main__ as cli
from phoenix.records import ROOT

REFERENCE = ROOT / "examples/retention/server.py"
V1_REFERENCE = ROOT / "examples/reference/server.py"
CHECK_IDS = {"V01", "V02", "V03", "V04", "V05", "V06", "V07", "V08"}
ARTIFACTS = {"server.py", "predecessor.py", "contract.md", "verifier.py", "baseline.py", "report.json"}
BINDINGS = {"server.py": "candidate_sha256", "predecessor.py": "predecessor_sha256",
            "contract.md": "contract_sha256", "verifier.py": "verifier_sha256",
            "baseline.py": "shared_subject_sha256"}


class ContinuityTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(importlib.util.find_spec("phoenix.continuity"),
                             "The separate v2 continuity verifier is not implemented")
        self.continuity = importlib.import_module("phoenix.continuity")
        self.temporary = tempfile.TemporaryDirectory(prefix="phoenix-continuity-test-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        ledger = ROOT / "docs/runs.json"
        before = ledger.read_bytes()
        self.addCleanup(lambda: self.assertEqual(ledger.read_bytes(), before,
                                                "Continuity checks must not append or rewrite AI ledger rows"))

    def assert_complete_bundle(self, directory, report):
        self.assertEqual({path.name for path in directory.iterdir()}, ARTIFACTS | {"manifest.json"})
        manifest = json.loads((directory / "manifest.json").read_bytes())
        self.assertEqual(set(manifest), ARTIFACTS)
        self.assertEqual(json.loads((directory / "report.json").read_bytes()), report)
        for name in ARTIFACTS:
            self.assertEqual(manifest[name], hashlib.sha256((directory / name).read_bytes()).hexdigest(), name)
        for name, field in BINDINGS.items():
            self.assertEqual(report[field], manifest[name], field)

    def bundle(self):
        # Share only an immutable test fixture; each negative control changes its own copy.
        cls = type(self)
        if not hasattr(cls, "fixture_directory"):
            cls.fixture_temporary = tempfile.TemporaryDirectory(prefix="phoenix-continuity-fixture-")
            cls.addClassCleanup(cls.fixture_temporary.cleanup)
            cls.fixture_directory = Path(cls.fixture_temporary.name) / "frozen"
            cls.fixture_report = self.continuity.verify_continuity(REFERENCE, REFERENCE,
                                                                  output_dir=cls.fixture_directory)
            self.assertEqual(cls.fixture_report["status"], "passed", cls.fixture_report)
            self.assert_complete_bundle(cls.fixture_directory, cls.fixture_report)
        destination = Path(tempfile.mkdtemp(prefix="bundle-", dir=str(self.root)))
        shutil.copytree(cls.fixture_directory, destination, dirs_exist_ok=True)
        return destination

    def rewrite_report(self, directory, report):
        path = directory / "report.json"
        path.write_text(json.dumps(report), encoding="utf-8")
        manifest_path = directory / "manifest.json"
        manifest = json.loads(manifest_path.read_bytes())
        manifest["report.json"] = hashlib.sha256(path.read_bytes()).hexdigest()
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    def mark_archived_execution(self, directory):
        # The real archived modules write the marker if replay loads either before validation.
        marker = self.root / "archive-executed.txt"
        report = json.loads((directory / "report.json").read_bytes())
        manifest_path = directory / "manifest.json"
        manifest = json.loads(manifest_path.read_bytes())
        statement = ("\nfrom pathlib import Path as _ContinuityTestMarker\n"
                     "_ContinuityTestMarker(%r).write_text('executed', encoding='utf-8')\n" % str(marker))
        for name in ("verifier.py", "baseline.py"):
            path = directory / name
            path.write_bytes(path.read_bytes() + statement.encode("utf-8"))
            manifest[name] = hashlib.sha256(path.read_bytes()).hexdigest()
            report[BINDINGS[name]] = manifest[name]
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        self.rewrite_report(directory, report)
        return marker

    def assert_replay_refused(self, directory, marker):
        with self.assertRaises((ValueError, RuntimeError, OSError)):
            self.continuity.replay_continuity(directory)
        self.assertFalse(marker.exists(), "Replay executed archived code before validating the complete bundle")

    def fault(self, name, old, new):
        source = REFERENCE.read_text(encoding="utf-8")
        self.assertEqual(source.count(old), 1, "The fault must change one known service behavior")
        candidate = self.root / (name + ".py")
        candidate.write_text(source.replace(old, new), encoding="utf-8")
        return candidate

    def command(self, *arguments):
        output, errors = io.StringIO(), io.StringIO()
        with patch.object(sys, "argv", ["phoenix"] + list(arguments)), \
                contextlib.redirect_stdout(output), contextlib.redirect_stderr(errors):
            status = cli.main()
        return status, output.getvalue(), errors.getvalue()

    def test_v2_reference_preserves_all_eight_continuity_behaviors(self):
        report = self.continuity.verify_continuity(REFERENCE, REFERENCE)
        self.assertEqual(report["status"], "passed", report)
        self.assertEqual((report["passed"], report["total"]), (8, 8))
        self.assertEqual({check["id"] for check in report["checks"]}, CHECK_IDS)
        self.assertNotEqual(report.get("kind"), "ai-rebuild")

    def test_destructive_and_behavioral_handoff_faults_fail_the_relevant_checks_and_remain_archived(self):
        startup = '        db.execute("PRAGMA user_version=2")'
        duplicate = 'return self.send_json(200, {"id": old["id"], "duplicate": True})'
        cases = (
            ("wiped-policies", startup,
             startup + '\n        db.execute("DELETE FROM retention_policies")', "V02"),
            ("implicit-startup-expiry", startup,
             startup + '\n        db.execute("DELETE FROM events WHERE id=(SELECT id FROM events ORDER BY received_at,id LIMIT 1)")', "V02"),
            ("incorrect-policy-response", '{"max_age_seconds": policy[0] if policy else None}',
             '{"max_age_seconds": None}', "V03"),
            ("broken-duplicate-identity", duplicate,
             'return self.send_json(200, {"id": "changed-" + old["id"], "duplicate": True})', "V04"),
        )
        for name, old, new, failed_id in cases:
            with self.subTest(fault=name):
                candidate = self.fault(name, old, new)
                directory = self.root / (name + "-bundle")
                report = self.continuity.verify_continuity(candidate, REFERENCE, output_dir=directory)
                self.assertEqual(report["status"], "failed", report)
                checks = {check["id"]: check for check in report["checks"]}
                self.assertTrue(checks["V01"]["passed"], report)
                self.assertFalse(checks[failed_id]["passed"], report)
                if failed_id != "V02":
                    self.assertTrue(checks["V02"]["passed"], "A behavior-only fault must reach the HTTP continuity checks")
                self.assertEqual(report["passed"], sum(check["passed"] for check in report["checks"]))
                self.assertEqual(report["total"], len(report["checks"]))
                self.assert_complete_bundle(directory, report)
                self.assertEqual((directory / "server.py").read_bytes(), candidate.read_bytes())

    def test_v1_predecessor_fails_before_the_candidate_runs_and_retains_a_complete_bundle(self):
        marker = self.root / "candidate-executed.txt"
        candidate = self.root / "candidate.py"
        candidate.write_text("from pathlib import Path\nPath(%r).write_text('executed')\n" % str(marker)
                             + REFERENCE.read_text(encoding="utf-8"), encoding="utf-8")
        directory = self.root / "failed-v1-bundle"
        report = self.continuity.verify_continuity(candidate, V1_REFERENCE, output_dir=directory)
        self.assertEqual(report["status"], "failed", report)
        self.assertFalse(next(check for check in report["checks"] if check["id"] == "V01")["passed"])
        self.assertFalse(marker.exists(), "A v1 fixture must not be handed to the v2 continuity candidate")
        self.assert_complete_bundle(directory, report)

    def test_source_paths_can_change_after_capture_without_changing_the_execution_or_bundle(self):
        candidate = self.root / "candidate.py"
        previous = self.root / "previous.py"
        candidate_source = REFERENCE.read_bytes()
        candidate.write_bytes(candidate_source)
        overwrite = ("from pathlib import Path\n"
                     "Path(%r).write_text(\"raise RuntimeError('reopened original candidate')\\n\")\n"
                     "Path(%r).write_text(\"raise RuntimeError('reopened original predecessor')\\n\")\n"
                     % (str(candidate), str(previous)))
        previous_source = overwrite.encode("utf-8") + candidate_source
        previous.write_bytes(previous_source)
        directory = self.root / "frozen-inputs"
        report = self.continuity.verify_continuity(candidate, previous, output_dir=directory)
        self.assertEqual(report["status"], "passed", report)
        self.assertNotEqual(candidate.read_bytes(), candidate_source)
        self.assertNotEqual(previous.read_bytes(), previous_source)
        self.assertEqual((directory / "server.py").read_bytes(), candidate_source)
        self.assertEqual((directory / "predecessor.py").read_bytes(), previous_source)
        self.assert_complete_bundle(directory, report)

    def test_replay_runs_the_frozen_modules_and_leaves_the_bundle_unchanged(self):
        directory = self.bundle()
        marker = self.mark_archived_execution(directory)
        before = {str(path.relative_to(directory)): path.read_bytes()
                  for path in directory.rglob("*") if path.is_file()}
        report = self.continuity.replay_continuity(directory)
        self.assertEqual(report["status"], "passed", report)
        self.assertEqual((report["passed"], report["total"]), (8, 8))
        self.assertEqual({check["id"] for check in report["checks"]}, CHECK_IDS)
        self.assertTrue(marker.exists(), "Replay must evaluate the archived verifier/helper bytes")
        self.assertEqual(before, {str(path.relative_to(directory)): path.read_bytes()
                                  for path in directory.rglob("*") if path.is_file()})

    def test_missing_null_empty_or_incorrect_source_bindings_refuse_before_archived_execution(self):
        for field in BINDINGS.values():
            for bad in ("missing", None, "", "0" * 64):
                with self.subTest(binding=field, bad=bad):
                    directory = self.bundle()
                    marker = self.mark_archived_execution(directory)
                    report = json.loads((directory / "report.json").read_bytes())
                    if bad == "missing":
                        del report[field]
                    else:
                        report[field] = bad
                    self.rewrite_report(directory, report)
                    self.assert_replay_refused(directory, marker)

    def test_manifest_tampering_and_missing_artifacts_refuse_before_archived_execution(self):
        for name in ARTIFACTS | {"manifest.json"}:
            for operation in ("missing", "changed"):
                with self.subTest(artifact=name, operation=operation):
                    directory = self.bundle()
                    marker = self.mark_archived_execution(directory)
                    path = directory / name
                    if operation == "missing":
                        path.unlink()
                    else:
                        path.write_bytes(path.read_bytes() + b"\n# changed artifact\n")
                    self.assert_replay_refused(directory, marker)

    def test_rehashed_manifest_cannot_hide_a_source_to_report_binding_mismatch(self):
        for name in BINDINGS:
            with self.subTest(artifact=name):
                directory = self.bundle()
                marker = self.mark_archived_execution(directory)
                path = directory / name
                path.write_bytes(path.read_bytes() + b"\n# changed despite a consistent manifest\n")
                manifest_path = directory / "manifest.json"
                manifest = json.loads(manifest_path.read_bytes())
                manifest[name] = hashlib.sha256(path.read_bytes()).hexdigest()
                manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
                self.assert_replay_refused(directory, marker)

    def test_directory_and_artifact_symlinks_refuse_before_archived_execution(self):
        for name in (None,) + tuple(sorted(ARTIFACTS | {"manifest.json"})):
            with self.subTest(link=name or "bundle directory"):
                directory = self.bundle()
                marker = self.mark_archived_execution(directory)
                if name is None:
                    link = self.root / "linked-bundle"
                    link.symlink_to(directory, target_is_directory=True)
                    directory = link
                else:
                    path = directory / name
                    target = self.root / (name + ".linked-target")
                    target.write_bytes(path.read_bytes())
                    path.unlink()
                    path.symlink_to(target)
                self.assert_replay_refused(directory, marker)

    def test_existing_output_directories_and_files_are_never_overwritten(self):
        for is_directory in (True, False):
            with self.subTest(directory=is_directory):
                target = self.root / ("existing-directory" if is_directory else "existing-file")
                if is_directory:
                    target.mkdir()
                    sentinel = target / "keep.txt"
                else:
                    sentinel = target
                sentinel.write_bytes(b"existing evidence must survive\n")
                with self.assertRaises((ValueError, RuntimeError, OSError)):
                    self.continuity.verify_continuity(REFERENCE, REFERENCE, output_dir=target)
                self.assertEqual(sentinel.read_bytes(), b"existing evidence must survive\n")
                if is_directory:
                    self.assertEqual({path.name for path in target.iterdir()}, {"keep.txt"})

    def test_cli_reports_passed_failed_and_refused_outcomes_without_changing_the_ai_ledger(self):
        directory = self.root / "cli-bundle"
        status, output, errors = self.command("verify-continuity", "--candidate", str(REFERENCE),
                                               "--predecessor", str(REFERENCE), "--output-dir", str(directory))
        self.assertEqual(status, 0, errors)
        self.assertEqual(json.loads(output)["passed"], 8)
        status, output, errors = self.command("replay-continuity", str(directory))
        self.assertEqual(status, 0, errors)
        self.assertEqual(json.loads(output)["total"], 8)
        status, output, errors = self.command("verify-continuity", "--candidate", str(REFERENCE),
                                               "--predecessor", str(V1_REFERENCE))
        self.assertEqual(status, 1, errors)
        self.assertEqual(json.loads(output)["status"], "failed")
        (directory / "baseline.py").write_bytes(b"raise RuntimeError('unchecked source')\n")
        status, _, errors = self.command("replay-continuity", str(directory))
        self.assertEqual(status, 2)
        self.assertTrue(errors)

    def test_smoke_retains_fresh_failed_handoff_outcomes(self):
        smoke = importlib.import_module("continuity_smoke")
        examples = self.root / "examples/retention"
        examples.mkdir(parents=True)
        shutil.copyfile(REFERENCE, examples / "server.py")
        maintenance = self.root / "docs/maintenance"
        maintenance.mkdir(parents=True)
        (self.root / "docs/runs.json").write_text("[]\n")
        line = '        db.execute("PRAGMA user_version=2")'
        bad = self.fault("smoke-policy-wiper", line, line + '\n        db.execute("DELETE FROM retention_policies")')
        bundle = maintenance / "failed-continuity"
        archived = self.continuity.verify_continuity(bad, REFERENCE, bundle)
        self.assertEqual(archived["status"], "failed")
        shutil.copytree(bundle, maintenance / "failed-continuity-negative")
        output = io.StringIO()
        with patch.object(smoke, "ROOT", self.root), contextlib.redirect_stdout(output):
            smoke.main()
        result = json.loads(output.getvalue().split("CONTINUITY_EVIDENCE=", 1)[1])
        self.assertEqual(len(result["observations"]), 2)
        self.assertTrue(all(item["report"]["status"] == "failed" for item in result["observations"]))
        self.assertFalse(result["public_ledger_changed"])


if __name__ == "__main__":
    unittest.main()
