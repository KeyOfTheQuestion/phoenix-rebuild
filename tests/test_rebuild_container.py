"""Generator controls use synthetic CLI responses, never a model or public ledger."""
import contextlib
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
from phoenix import rebuild as adapter
from phoenix.audit import audit, CHECK_IDS
from phoenix.container import ContainerError
from phoenix.records import ROOT


class ContainerRebuildTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="phoenix-rebuild-test-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        for name in ("specs", "phoenix", "docs"):
            (self.root / name).mkdir()
        self.contract = b"Synthetic public contract.\n"
        self.verifier = b"raise RuntimeError('the container verifier must not load on the host')\n"
        (self.root / "specs/webhook-inbox.md").write_bytes(self.contract)
        (self.root / "phoenix/verify.py").write_bytes(self.verifier)
        (self.root / "phoenix/empty_candidate.py").write_text("# no generated source\n")
        self.predecessor = self.root / "previous.py"
        self.previous = b"# synthetic previous implementation\n"
        self.predecessor.write_bytes(self.previous)
        self.code = "# complete synthetic generator output\n"
        self.generator_calls = []
        self.generation_failure = None
        self.mutate = False
        for target in ("phoenix.rebuild.ROOT", "phoenix.records.ROOT"):
            change = patch(target, self.root)
            change.start()
            self.addCleanup(change.stop)
        self.which = patch("phoenix.rebuild.shutil.which", return_value="/synthetic/codex")
        self.which.start()
        self.addCleanup(self.which.stop)
        self.process = patch("phoenix.rebuild.subprocess.run", side_effect=self.fake_cli)
        self.run = self.process.start()
        self.addCleanup(self.process.stop)
        self.preflight = patch("phoenix.container.prepare_container")
        self.prepare = self.preflight.start()
        self.addCleanup(self.preflight.stop)
        self.runner_patch = patch("phoenix.container.evaluate_snapshot_in_container", side_effect=self.container_result)
        self.runner = self.runner_patch.start()
        self.addCleanup(self.runner_patch.stop)

    def fake_cli(self, command, **kwargs):
        if command[1:] == ["--version"]:
            return subprocess.CompletedProcess(command, 0, "synthetic-test-cli (not a model)\n", "")
        self.generator_calls.append(command)
        directory = Path(command[command.index("--cd") + 1])
        self.assertEqual({p.name for p in directory.iterdir()}, {"contract.md", "response-schema.json"})
        self.assertEqual((directory / "contract.md").read_bytes(), self.contract)
        self.assertIn("--ignore-user-config", command)
        self.assertNotIn("--model", command)
        self.assertEqual(kwargs["timeout"], 300)
        if self.generation_failure:
            raise self.generation_failure
        if self.mutate:
            (self.root / "phoenix/verify.py").write_bytes(b"# changed during generation\n")
            (self.root / "specs/webhook-inbox.md").write_bytes(b"changed contract")
            self.predecessor.write_bytes(b"# changed predecessor\n")
        answer = Path(command[command.index("--output-last-message") + 1])
        answer.write_text(json.dumps({"server_py": self.code}))
        return subprocess.CompletedProcess(command, 0, json.dumps({"type": "turn.completed", "usage": None}), "")

    def container_result(self, candidate, verifier, predecessor_source=None, timeout=90):
        self.assertEqual(candidate, self.code.encode())
        self.assertEqual(verifier, self.verifier)
        self.assertEqual(predecessor_source, self.previous)
        self.assertEqual(timeout, 37)
        return {"schema_version": 1, "kind": "reference-verification", "status": "passed",
                "started_at": "synthetic-unit-control", "passed": 19, "total": 19,
                "checks": [{"id": key, "passed": True} for key in sorted(CHECK_IDS)],
                "execution": "docker-container", "duration_seconds": 0,
                "predecessor_sha256": hashlib.sha256(predecessor_source).hexdigest()}

    def rebuild(self, **kwargs):
        with contextlib.redirect_stdout(io.StringIO()):
            return adapter.rebuild(predecessor=self.predecessor, use_container=True,
                                   verification_timeout=37, **kwargs)

    def test_preflight_failure_spends_no_generation_usage_and_publishes_no_attempt(self):
        self.prepare.side_effect = ContainerError("unavailable synthetic Docker runtime")
        with self.assertRaisesRegex(ContainerError, "unavailable"):
            self.rebuild()
        self.run.assert_not_called()
        self.runner.assert_not_called()
        self.assertFalse((self.root / "docs/runs.json").exists())

    def test_generated_candidate_uses_frozen_inputs_without_loading_the_verifier_on_host(self):
        self.mutate = True
        report, = self.rebuild()
        self.prepare.assert_called_once_with(37)
        self.runner.assert_called_once()
        self.assertEqual(report["status"], "passed")
        self.assertEqual(report["kind"], "ai-rebuild")
        self.assertEqual(report["human_interventions"], 0)
        self.assertEqual(report["provenance"]["requested_verification"], "docker-container")
        self.assertEqual(report["provenance"]["verification_timeout_seconds"], 37)
        self.assertEqual(report["execution"], "docker-container")
        directory = self.root / "docs/evidence" / report["id"]
        for name, source in (("server.py", self.code.encode()), ("verifier.py", self.verifier),
                             ("predecessor.py", self.previous), ("contract.md", self.contract)):
            self.assertEqual((directory / name).read_bytes(), source)
        self.assertEqual(audit(self.root / "docs"), [])

    def test_runtime_failure_keeps_generated_code_without_host_fallback_or_retry(self):
        for error in (ContainerError("Container deadline exceeded"), OSError("/private/synthetic-path")):
            with self.subTest(error=type(error).__name__):
                self.runner.side_effect = error
                before = len(self.generator_calls)
                report, = self.rebuild(attempts=2)
                self.assertEqual(len(self.generator_calls), before + 1)
                self.assertEqual(report["status"], "verification-error")
                self.assertEqual((report["passed"], report["total"]), (0, 0))
                self.assertEqual(report["checks"], [])
                self.assertNotIn("/private/synthetic-path", report["error"])
                directory = self.root / "docs/evidence" / report["id"]
                self.assertEqual((directory / "server.py").read_text(), self.code)
                self.assertEqual((directory / "predecessor.py").read_bytes(), self.previous)
                self.assertEqual(audit(self.root / "docs"), [])

    def test_acceptance_failure_can_retry_but_retains_both_attempts(self):
        passing = self.container_result(self.code.encode(), self.verifier, self.previous, 37)
        failing = dict(passing, status="failed", passed=0, total=1,
                       checks=[{"id": "C01", "title": "Synthetic acceptance failure", "passed": False}])
        self.runner.side_effect = [failing, passing]
        first, second = self.rebuild(attempts=2)
        self.assertEqual([first["status"], second["status"]], ["failed", "passed"])
        self.assertEqual(len(self.generator_calls), 2)
        self.assertEqual(audit(self.root / "docs"), [])

    def test_generation_timeout_records_requested_mode_without_starting_acceptance(self):
        self.generation_failure = subprocess.TimeoutExpired("synthetic generator", 300)
        report, = self.rebuild()
        self.assertEqual(report["status"], "generation-failed")
        self.assertEqual(report["execution"], "not-executed")
        self.assertEqual(report["provenance"]["requested_verification"], "docker-container")
        self.assertFalse(report["provenance"]["user_configuration_loaded"])
        self.runner.assert_not_called()
        self.assertEqual(audit(self.root / "docs"), [])

    def test_default_local_mode_does_not_prepare_or_contact_docker(self):
        self.verifier = b'''def evaluate(candidate, kind, predecessor):
 return {"status":"failed","kind":kind,"checks":[],"passed":0,"total":0,"execution":"local-subprocess"}
'''
        (self.root / "phoenix/verify.py").write_bytes(self.verifier)
        with contextlib.redirect_stdout(io.StringIO()):
            report, = adapter.rebuild(attempts=1, predecessor=self.predecessor)
        self.prepare.assert_not_called()
        self.runner.assert_not_called()
        self.assertEqual(report["execution"], "local-subprocess")
        self.assertEqual(report["provenance"]["requested_verification"], "local-subprocess")
        self.assertEqual(audit(self.root / "docs"), [])

    def test_cli_forwards_container_options_and_returns_two_for_runtime_error(self):
        with patch.object(sys, "argv", ["phoenix", "rebuild", "--container", "--verification-timeout", "37"]), \
                patch.object(adapter, "rebuild", return_value=[{"status": "verification-error"}]) as rebuild:
            self.assertEqual(cli.main(), 2)
        self.assertEqual(rebuild.call_args.kwargs, {"use_container": True, "verification_timeout": 37})

    def test_audit_rejects_runtime_error_that_claims_acceptance_checks(self):
        self.runner.side_effect = ContainerError("synthetic runtime failure")
        report, = self.rebuild()
        directory = self.root / "docs/evidence" / report["id"]
        report.update(checks=[{"id": "C01", "passed": False}], total=1)
        (directory / "report.json").write_text(json.dumps(report))
        manifest = json.loads((directory / "manifest.json").read_text())
        manifest["report.json"] = hashlib.sha256((directory / "report.json").read_bytes()).hexdigest()
        (directory / "manifest.json").write_text(json.dumps(manifest))
        ledger = json.loads((self.root / "docs/runs.json").read_text())
        ledger[0]["total"] = 1
        (self.root / "docs/runs.json").write_text(json.dumps(ledger))
        self.assertTrue(any("must not claim an acceptance verdict" in error for error in audit(self.root / "docs")))


if __name__ == "__main__":
    unittest.main()
