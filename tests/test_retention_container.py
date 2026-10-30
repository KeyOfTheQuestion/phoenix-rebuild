"""Contract-002 replay controls use a synthetic Docker boundary, never a model."""
import contextlib
import copy
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
from phoenix.container import ContainerError, evaluate_snapshot_in_container
from phoenix.audit import verified_run
from phoenix.records import ROOT


class RetentionContainerTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="phoenix-retention-container-test-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.row = next(row for row in json.loads((ROOT / "docs/runs.json").read_text())
                        if row.get("contract_id") == "002" and row["status"] == "passed")
        self.original, self.artifacts = verified_run(ROOT / "docs", self.row["id"])
        directory = self.root / "docs/evidence" / self.row["id"]
        shutil.copytree(ROOT / "docs/evidence" / self.row["id"], directory)
        (self.root / "docs/runs.json").write_text(json.dumps([self.row]))
        self.calls = []
        self.staged = None
        self.changes = {}

    def fake_docker(self, command, **kwargs):
        self.calls.append(command)
        if command[1:3] == ["image", "inspect"]:
            return subprocess.CompletedProcess(command, 0, "sha256:" + "a" * 64, "")
        if command[1] == "rm":
            return subprocess.CompletedProcess(command, 0, "", "")
        self.assertEqual(command[1], "run")
        stage = Path(command[command.index("--mount") + 1].split("source=", 1)[1].split(",target=", 1)[0])
        self.staged = {p.name: p.read_bytes() for p in stage.iterdir()}
        result = copy.deepcopy(self.original)
        result["container_probe"] = {
            "uid": 65534, "interfaces": ["lo"], "root_read_only": True,
            "input_read_only": True, "effective_capabilities": 0, "no_new_privileges": True,
            "tmpfs_bytes": 67108864, "tmpfs_restrictions": True,
            "memory_bytes": 268435456, "swap_bytes": 0, "pids": 128,
            "docker_socket_present": False, "host_sentinel_present": False,
            "input_files": sorted(self.staged), "cpu_quota": ["100000", "100000"],
        }
        result.update(self.changes)
        return subprocess.CompletedProcess(command, 0, json.dumps(result), "")

    def replay(self):
        output, errors = io.StringIO(), io.StringIO()
        with patch.object(cli, "ROOT", self.root), \
                patch.object(sys, "argv", ["phoenix", "replay", self.row["id"], "--container"]), \
                patch("phoenix.container.shutil.which", return_value="/synthetic/docker"), \
                patch("phoenix.container.subprocess.run", side_effect=self.fake_docker), \
                contextlib.redirect_stdout(output), contextlib.redirect_stderr(errors):
            code = cli.main()
        return code, output.getvalue(), errors.getvalue()

    def test_cli_stages_the_complete_verified_package_without_loading_host_verifiers(self):
        before = {p.name: p.read_bytes() for p in (self.root / "docs/evidence" / self.row["id"]).iterdir()}
        with patch("phoenix.retention_archive.replay_frozen_retention", side_effect=AssertionError("host execution")):
            code, output, errors = self.replay()
        self.assertEqual(code, 0, errors)
        result = json.loads(output)
        self.assertEqual((result["passed"], result["total"], result["execution"]), (32, 32, "docker-container"))
        self.assertEqual(result["replay_source"], "verified-archive-bytes")
        self.assertEqual(result["replay_run_id"], self.row["id"])
        self.assertEqual(set(self.staged), set(self.artifacts) - {"report.json"} | {"entrypoint.py"})
        for name, raw in self.artifacts.items():
            if name != "report.json":
                self.assertEqual(self.staged[name], raw, name)
        self.assertEqual(before, {p.name: p.read_bytes() for p in (self.root / "docs/evidence" / self.row["id"]).iterdir()})

    def test_changed_archive_after_validation_cannot_replace_staged_helpers(self):
        def checked_then_changed(site, run_id):
            checked = verified_run(site, run_id)
            for name in ("server.py", "verifier.py", "baseline.py", "retention.py", "records.py", "audit.py", "contract.md"):
                (self.root / "docs/evidence" / run_id / name).write_bytes(b"raise RuntimeError('unchecked source')\n")
            return checked
        with patch("phoenix.audit.verified_run", side_effect=checked_then_changed):
            code, _, errors = self.replay()
        self.assertEqual(code, 0, errors)
        self.assertEqual(self.staged["baseline.py"], self.artifacts["baseline.py"])

    def test_wrong_dependency_hashes_profile_schema_or_coverage_cannot_claim_success(self):
        changes = [{field: "0" * 64} for field in (
            "candidate_sha256", "predecessor_sha256", "verifier_sha256", "contract_sha256",
            "shared_subject_sha256", "retention_verifier_sha256", "records_support_sha256", "audit_support_sha256")]
        changes += [{"contract_id": "001"}, {"contract_version": "1.0.0"}, {"verifier_profile": "unknown"},
                    {"kind": "ai-rebuild"}, {"baseline_expected_schema_version": True}, {"expected_total": 19},
                    {"checks": self.original["checks"][:-1], "passed": 31, "total": 31}]
        for change in changes:
            with self.subTest(field=tuple(change)):
                self.calls = []
                self.changes = change
                code, output, errors = self.replay()
                self.assertEqual(code, 2)
                self.assertEqual(output, "")
                self.assertIn("container", errors.lower())
                self.assertEqual(self.calls[-1][1:3], ["rm", "--force"])

    def test_incomplete_unsafe_or_mutable_packages_fail_before_docker(self):
        support = {name: self.artifacts[name] for name in
                   ("baseline.py", "retention.py", "records.py", "audit.py", "contract.md")}
        inputs = [(None, self.artifacts["predecessor.py"]),
                  ({key: value for key, value in support.items() if key != "audit.py"}, self.artifacts["predecessor.py"]),
                  (dict(support, **{"../escape.py": b"# unsafe"}), self.artifacts["predecessor.py"]),
                  (dict(support, **{"entrypoint.py": b"# overwrite"}), self.artifacts["predecessor.py"]),
                  (dict(support, **{"audit.py": bytearray(b"# mutable")}), self.artifacts["predecessor.py"]),
                  (support, None)]
        for dependencies, previous in inputs:
            with self.subTest(dependencies=tuple(dependencies or {})), \
                    patch("phoenix.container.subprocess.run") as run:
                with self.assertRaises(ContainerError):
                    evaluate_snapshot_in_container(self.artifacts["server.py"], self.artifacts["verifier.py"],
                                                   predecessor_source=previous, verifier_profile="retention-composed-v1",
                                                   support_sources=dependencies)
                run.assert_not_called()

    def test_tampered_helper_is_rejected_before_docker_is_contacted(self):
        (self.root / "docs/evidence" / self.row["id"] / "retention.py").write_bytes(b"# tampered\n")
        code, output, errors = self.replay()
        self.assertEqual(code, 2)
        self.assertEqual(output, "")
        self.assertIn("digest mismatch", errors)
        self.assertEqual(self.calls, [])


if __name__ == "__main__":
    unittest.main()
