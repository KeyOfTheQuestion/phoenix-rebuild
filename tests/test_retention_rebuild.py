"""Synthetic CLI controls for contract-002 generation; no model or public fixtures."""
import contextlib
import hashlib
import io
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from phoenix import __main__ as cli
from phoenix import rebuild as adapter
from phoenix.audit import audit, verified_run, RETENTION_CHECK_IDS
from phoenix.container import ContainerError
from phoenix.records import ROOT


class RetentionRebuildTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="phoenix-v2-generator-test-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        (self.root / "specs").mkdir()
        (self.root / "phoenix").mkdir()
        for name in ("verify.py", "retention.py", "records.py", "audit.py", "empty_candidate.py"):
            shutil.copyfile(ROOT / "phoenix" / name, self.root / "phoenix" / name)
        self.contract = (ROOT / "specs/webhook-inbox-retention.md").read_bytes()
        (self.root / "specs/webhook-inbox-retention.md").write_bytes(self.contract)
        self.previous = (ROOT / "examples/reference/server.py").read_bytes()
        self.predecessor = self.root / "previous.py"
        self.predecessor.write_bytes(self.previous)
        self.code = (ROOT / "examples/retention/server.py").read_text()
        self.prompts = []
        self.mutate = False
        self.failure = None
        self.answers = []
        self.before = {name: (self.root / "phoenix" / name).read_bytes()
                       for name in ("verify.py", "retention.py", "records.py", "audit.py")}
        for target in ("phoenix.rebuild.ROOT", "phoenix.records.ROOT", "phoenix.__main__.ROOT"):
            change = patch(target, self.root)
            change.start()
            self.addCleanup(change.stop)
        change = patch("phoenix.rebuild.shutil.which", return_value="/synthetic/codex")
        change.start()
        self.addCleanup(change.stop)
        change = patch("phoenix.rebuild.subprocess.run", side_effect=self.fake_cli)
        self.cli_process = change.start()
        self.addCleanup(change.stop)

    def fake_cli(self, command, **kwargs):
        if command[1:] == ["--version"]:
            return subprocess.CompletedProcess(command, 0, "synthetic-v2-fixture (no model call)\n", "")
        workspace = Path(command[command.index("--cd") + 1])
        self.assertEqual({p.name for p in workspace.iterdir()}, {"contract.md", "response-schema.json"})
        self.assertEqual((workspace / "contract.md").read_bytes(), self.contract)
        self.assertIn("--ignore-user-config", command)
        self.assertNotIn("--model", command)
        self.assertEqual(kwargs["timeout"], 300)
        self.prompts.append(kwargs["input"])
        if self.mutate:
            for name in self.before:
                (self.root / "phoenix" / name).write_bytes(b"raise RuntimeError('changed during generation')\n")
            (self.root / "specs/webhook-inbox-retention.md").write_bytes(b"changed contract")
            self.predecessor.write_bytes(b"changed predecessor")
        if self.failure:
            raise self.failure
        code = self.answers.pop(0) if self.answers else self.code
        Path(command[command.index("--output-last-message") + 1]).write_text(json.dumps({"server_py": code}))
        return subprocess.CompletedProcess(command, 0, json.dumps({"type": "turn.completed", "usage": None}), "")

    def rebuild(self, **kwargs):
        with contextlib.redirect_stdout(io.StringIO()):
            return adapter.rebuild(predecessor=self.predecessor, contract_id="002", **kwargs)

    def test_v2_generation_freezes_complete_inputs_and_publishes_a_distinct_32_check_ai_profile(self):
        self.mutate = True
        report, = self.rebuild(attempts=1)
        self.assertEqual((report["kind"], report["contract_id"], report["verifier_profile"]),
                         ("ai-rebuild", "002", "retention-ai-v1"))
        self.assertEqual((report["passed"], report["total"]), (32, 32))
        self.assertEqual(report["provenance"]["version"], "synthetic-v2-fixture (no model call)")
        self.assertEqual(report["human_interventions"], 0)
        checked, artifacts = verified_run(self.root / "docs", report["id"])
        self.assertEqual(len(artifacts), 10)
        self.assertEqual(artifacts["contract.md"], self.contract)
        self.assertEqual(artifacts["predecessor.py"], self.previous)
        for name in self.before:
            artifact = "baseline.py" if name == "verify.py" else name
            self.assertEqual(artifacts[artifact], self.before[name])
        before = dict(artifacts)
        output = io.StringIO()
        with patch.object(sys, "argv", ["phoenix", "replay", report["id"]]), contextlib.redirect_stdout(output):
            self.assertEqual(cli.main(), 0)
        self.assertEqual(json.loads(output.getvalue())["kind"], "ai-rebuild")
        self.assertEqual(verified_run(self.root / "docs", report["id"])[1], before)

    def test_failed_candidate_is_kept_and_retry_gets_feedback_without_previous_source(self):
        self.answers = ["raise RuntimeError('synthetic failed candidate')\n", self.code]
        first, second = self.rebuild(attempts=2)
        self.assertEqual([first["status"], second["status"]], ["failed", "passed"])
        self.assertEqual(len(self.prompts), 2)
        self.assertIn("failed these checks", self.prompts[1])
        self.assertNotIn("synthetic failed candidate", self.prompts[1])
        self.assertEqual(audit(self.root / "docs"), [])

    def test_generation_failure_retains_a_complete_frozen_ai_bundle_with_no_verdict(self):
        self.failure = subprocess.TimeoutExpired("synthetic CLI timeout", 300)
        report, = self.rebuild(attempts=2)
        self.assertEqual(report["status"], "generation-failed")
        self.assertEqual((report["passed"], report["total"]), (0, 0))
        self.assertEqual(len(self.prompts), 1)
        self.assertEqual(audit(self.root / "docs"), [])
        self.assertIn("predecessor.py", verified_run(self.root / "docs", report["id"])[1])

    def test_local_verifier_error_retains_generated_source_and_stops_without_retry(self):
        with patch("phoenix.retention_archive.replay_frozen_retention", side_effect=RuntimeError("private runtime diagnostic")):
            report, = self.rebuild(attempts=2)
        self.assertEqual(report["status"], "verification-error")
        self.assertEqual(report["execution"], "local-subprocess-attempted")
        self.assertEqual(len(self.prompts), 1)
        self.assertNotIn("private runtime diagnostic", report["error"])
        self.assertEqual((self.root / "docs/evidence" / report["id"] / "server.py").read_text(), self.code)
        self.assertEqual(audit(self.root / "docs"), [])

    def test_v2_container_preflight_failure_spends_no_generation_usage(self):
        with patch("phoenix.container.prepare_container", side_effect=ContainerError("synthetic unavailable Docker")) as prepare:
            try:
                self.rebuild(use_container=True, verification_timeout=37)
            except RuntimeError as error:
                self.assertIsInstance(error, ContainerError)
            else:
                self.fail("Unavailable Docker must refuse before provider use")
        prepare.assert_called_once_with(37)
        self.cli_process.assert_not_called()
        self.assertFalse((self.root / "docs/runs.json").exists())

    def container_result(self, candidate, verifier, predecessor_source=None, timeout=90,
                         *, verifier_profile=None, support_sources=None):
        self.assertEqual(candidate, self.code.encode())
        self.assertEqual(predecessor_source, self.previous)
        self.assertEqual(timeout, 37)
        self.assertEqual(verifier_profile, "retention-ai-v1")
        self.assertIn(b'evaluate_reference = evaluate', verifier)
        expected = {("baseline.py" if name == "verify.py" else name): raw for name, raw in self.before.items()}
        self.assertEqual(support_sources, dict(expected, **{"contract.md": self.contract}))
        return {"schema_version": 1, "kind": "ai-rebuild", "status": "passed", "started_at": "synthetic-unit-control",
                "passed": 32, "total": 32, "checks": [{"id": key, "passed": True} for key in sorted(RETENTION_CHECK_IDS)],
                "execution": "docker-container", "duration_seconds": 0}

    def container_rebuild(self, **kwargs):
        try:
            return self.rebuild(use_container=True, verification_timeout=37, **kwargs)
        except RuntimeError as error:
            self.fail("Contract-002 Docker acceptance was refused: " + str(error))

    def test_v2_container_uses_all_frozen_inputs_without_loading_archived_code_on_host(self):
        self.mutate = True
        with patch("phoenix.container.prepare_container") as prepare, \
                patch("phoenix.container.evaluate_snapshot_in_container", side_effect=self.container_result) as runner, \
                patch("phoenix.retention_archive.replay_frozen_retention", side_effect=AssertionError("No host evaluator")) as local:
            report, = self.container_rebuild(attempts=1)
        prepare.assert_called_once_with(37)
        runner.assert_called_once()
        local.assert_not_called()
        self.assertEqual((report["status"], report["passed"], report["total"]), ("passed", 32, 32))
        self.assertEqual(report["execution"], "docker-container")
        self.assertEqual(report["provenance"]["requested_verification"], "docker-container")
        self.assertEqual(report["provenance"]["verification_timeout_seconds"], 37)
        checked, artifacts = verified_run(self.root / "docs", report["id"])
        self.assertEqual(artifacts["contract.md"], self.contract)
        self.assertEqual(artifacts["predecessor.py"], self.previous)
        self.assertEqual(len(artifacts), 10)

    def test_v2_container_runtime_errors_keep_full_bundle_and_stop_without_host_fallback_or_retry(self):
        for error in (ContainerError("Synthetic invalid runtime report"), OSError("/private/synthetic-path")):
            with self.subTest(error=type(error).__name__), patch("phoenix.container.prepare_container"), \
                    patch("phoenix.container.evaluate_snapshot_in_container", side_effect=error), \
                    patch("phoenix.retention_archive.replay_frozen_retention", side_effect=AssertionError("No host fallback")) as local:
                before = len(self.prompts)
                report, = self.container_rebuild(attempts=2)
                self.assertEqual(len(self.prompts), before + 1)
                local.assert_not_called()
                self.assertEqual(report["status"], "verification-error")
                self.assertEqual(report["execution"], "docker-container-attempted")
                self.assertEqual((report["passed"], report["total"]), (0, 0))
                self.assertNotIn("/private/", report["error"])
                checked, artifacts = verified_run(self.root / "docs", report["id"])
                self.assertEqual(artifacts["server.py"], self.code.encode())
                self.assertEqual(len(artifacts), 10)

    def test_v2_container_acceptance_failure_keeps_both_attempts_and_only_passes_check_feedback(self):
        passing = self.container_result(self.code.encode(), b'evaluate_reference = evaluate', self.previous, 37,
                                       verifier_profile="retention-ai-v1", support_sources=dict(
                                           {("baseline.py" if name == "verify.py" else name): raw for name, raw in self.before.items()},
                                           **{"contract.md": self.contract}))
        failing = dict(passing, status="failed", passed=0, total=1,
                       checks=[{"id": "M01", "title": "Synthetic migration failure", "passed": False}])
        with patch("phoenix.container.prepare_container"), \
                patch("phoenix.container.evaluate_snapshot_in_container", side_effect=[failing, passing]):
            first, second = self.container_rebuild(attempts=2)
        self.assertEqual([first["status"], second["status"]], ["failed", "passed"])
        self.assertEqual(len(self.prompts), 2)
        self.assertIn("Synthetic migration failure", self.prompts[1])
        self.assertEqual(audit(self.root / "docs"), [])

    def test_v2_container_generation_timeout_retains_requested_mode_without_acceptance(self):
        self.failure = subprocess.TimeoutExpired("synthetic generator", 300)
        with patch("phoenix.container.prepare_container"), \
                patch("phoenix.container.evaluate_snapshot_in_container") as runner:
            report, = self.container_rebuild(attempts=2)
        runner.assert_not_called()
        self.assertEqual(len(self.prompts), 1)
        self.assertEqual(report["status"], "generation-failed")
        self.assertEqual(report["execution"], "not-executed")
        self.assertEqual(report["provenance"]["requested_verification"], "docker-container")
        self.assertEqual(audit(self.root / "docs"), [])

    def test_v2_ai_profile_cannot_drop_prompt_or_be_relabelled_as_reference(self):
        report, = self.rebuild(attempts=1)
        directory = self.root / "docs/evidence" / report["id"]
        row, = json.loads((self.root / "docs/runs.json").read_text())
        def replace(changed):
            path = directory / "report.json"
            path.write_text(json.dumps(changed))
            manifest = json.loads((directory / "manifest.json").read_text())
            manifest["report.json"] = hashlib.sha256(path.read_bytes()).hexdigest()
            (directory / "manifest.json").write_text(json.dumps(manifest))
            (self.root / "docs/runs.json").write_text(json.dumps([{key: changed.get(key) for key in row}]))
        for changes in ({"kind": "retention-reference-verification"}, {"verifier_profile": "retention-composed-v1"},
                        {"human_interventions": False}, {"provenance": {"generator": "reference-verification"}},
                        {"verifier_sha256": None}, {"verifier_sha256": ""}, {"verifier_sha256": "0" * 64},
                        {"checks": report["checks"][:-1], "passed": 31, "total": 31}):
            with self.subTest(changes=tuple(changes)):
                replace(dict(report, **changes))
                self.assertTrue(audit(self.root / "docs"))
        for field in ("candidate_sha256", "predecessor_sha256", "contract_sha256", "verifier_sha256",
                      "shared_subject_sha256", "retention_verifier_sha256", "records_support_sha256", "audit_support_sha256"):
            with self.subTest(missing_binding=field):
                changed = dict(report)
                del changed[field]
                replace(changed)
                self.assertTrue(audit(self.root / "docs"))
        replace(report)
        (directory / "prompt.md").unlink()
        manifest = json.loads((directory / "manifest.json").read_text())
        manifest.pop("prompt.md")
        (directory / "manifest.json").write_text(json.dumps(manifest))
        self.assertTrue(audit(self.root / "docs"))

    def test_invalid_generated_python_is_archived_without_an_acceptance_verdict(self):
        self.answers = ["for :\n"]
        report, = self.rebuild(attempts=1)
        self.assertEqual(report["status"], "generation-failed")
        self.assertEqual((report["passed"], report["total"]), (0, 0))
        self.assertEqual((self.root / "docs/evidence" / report["id"] / "server.py").read_text(), "for :\n")
        self.assertEqual(audit(self.root / "docs"), [])

    def test_cli_selects_contract_002_without_changing_the_default_model(self):
        with patch.object(sys, "argv", ["phoenix", "rebuild", "--contract", "002"]), \
                patch.object(adapter, "rebuild", return_value=[{"status": "failed"}]) as call:
            self.assertEqual(cli.main(), 1)
        self.assertEqual(call.call_args.kwargs["contract_id"], "002")
        self.assertIsNone(call.call_args.args[2])


if __name__ == "__main__":
    unittest.main()
