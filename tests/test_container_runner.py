import contextlib
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from phoenix import __main__ as cli
from phoenix import container
from phoenix.audit import CHECK_IDS


class ContainerRunnerTests(unittest.TestCase):
    """Unit controls use a fake Docker client; real execution is a separate CI check."""

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="phoenix-container-test-")
        self.addCleanup(self.temporary.cleanup)
        self.candidate = Path(self.temporary.name) / "candidate.py"
        self.candidate.write_text("# synthetic candidate snapshot\n")
        self.predecessor = Path(self.temporary.name) / "previous.py"
        self.predecessor.write_text("# synthetic predecessor snapshot\n")
        self.calls = []
        self.payload = None
        self.run_result = None
        self.boundary_change = None
        self.docker = patch("phoenix.container.shutil.which", return_value="/synthetic/docker")
        self.docker.start()
        self.addCleanup(self.docker.stop)

    def fake_docker(self, command, **kwargs):
        self.calls.append(command)
        if command[1:3] == ["image", "inspect"]:
            return subprocess.CompletedProcess(command, 0, "sha256:" + "a" * 64 + "\n", "")
        if command[1] == "rm":
            return subprocess.CompletedProcess(command, 0, "", "")
        self.assertEqual(command[1], "run")
        if isinstance(self.run_result, Exception):
            raise self.run_result
        if self.run_result is not None:
            return self.run_result
        mount = command[command.index("--mount") + 1]
        stage = Path(mount.split("source=", 1)[1].split(",target=", 1)[0])
        sources = {p.name: p.read_bytes() for p in stage.iterdir()}
        expected = {"server.py", "predecessor.py", "verifier.py", "entrypoint.py"}
        self.assertEqual(set(sources), expected)
        self.assertEqual(sources["server.py"], b"# synthetic candidate snapshot\n")
        self.assertEqual(sources["predecessor.py"], b"# synthetic predecessor snapshot\n")
        # Simulate files changing after staging: validation must retain the snapshot.
        self.candidate.write_text("# replaced after staging\n")
        probe = {"uid": 65534, "interfaces": ["lo"], "root_read_only": True,
                 "input_read_only": True, "effective_capabilities": 0, "no_new_privileges": True,
                 "tmpfs_bytes": 67108864, "tmpfs_restrictions": True,
                 "memory_bytes": 268435456, "swap_bytes": 0, "pids": 128,
                 "docker_socket_present": False, "host_sentinel_present": False,
                 "input_files": sorted(sources), "cpu_quota": ["100000", "100000"]}
        if self.boundary_change:
            probe.update(self.boundary_change)
        report = {"status": "passed", "checks": [{"id": key, "passed": True} for key in sorted(CHECK_IDS)],
                  "passed": 19, "total": 19, "container_probe": probe}
        import hashlib
        for name, field in (("server.py", "candidate_sha256"), ("predecessor.py", "predecessor_sha256"),
                            ("verifier.py", "verifier_sha256")):
            report[field] = hashlib.sha256(sources[name]).hexdigest()
        if self.payload:
            report.update(self.payload)
        return subprocess.CompletedProcess(command, 0, json.dumps(report), "")

    def evaluate(self):
        with patch("phoenix.container.subprocess.run", side_effect=self.fake_docker):
            return container.evaluate_in_container(self.candidate, predecessor=self.predecessor)

    def test_only_snapshots_are_mounted_and_the_host_environment_is_not_forwarded(self):
        with patch.dict("os.environ", {"PHOENIX_HOST_SENTINEL": "synthetic-host-only"}):
            report = self.evaluate()
        self.assertEqual(report["execution"], "docker-container")
        self.assertEqual(report["container_image"], container.IMAGE)
        command = next(c for c in self.calls if c[1] == "run")
        for flag, value in (("--network", "none"), ("--user", "65534:65534"), ("--cap-drop", "ALL"),
                            ("--security-opt", "no-new-privileges=true"), ("--memory", "256m"),
                            ("--memory-swap", "256m"), ("--cpus", "1"), ("--pids-limit", "128")):
            self.assertEqual(command[command.index(flag) + 1], value)
        self.assertIn("--read-only", command)
        self.assertNotIn("synthetic-host-only", " ".join(command))
        self.assertNotIn("--env-file", command)
        name = command[command.index("--name") + 1]
        self.assertEqual(self.calls[-1], ["/synthetic/docker", "rm", "--force", name])

    def test_missing_docker_refuses_execution_without_a_host_fallback(self):
        with patch("phoenix.container.shutil.which", return_value=None), patch("phoenix.container.subprocess.run") as run:
            with self.assertRaisesRegex(container.ContainerError, "did not run"):
                container.evaluate_in_container(self.candidate)
            run.assert_not_called()

    def test_timeout_requests_removal_of_the_entire_container(self):
        self.run_result = subprocess.TimeoutExpired("synthetic docker run", 90)
        with self.assertRaisesRegex(container.ContainerError, "cleanup was requested"):
            self.evaluate()
        self.assertEqual(self.calls[-1][1:3], ["rm", "--force"])

    def test_failed_process_or_invalid_json_cannot_return_a_verdict(self):
        for result in (subprocess.CompletedProcess([], 137, "", ""),
                       subprocess.CompletedProcess([], 0, "not JSON", "")):
            with self.subTest(result=result.returncode):
                self.run_result = result
                with self.assertRaises(container.ContainerError):
                    self.evaluate()
                self.assertEqual(self.calls[-1][1:3], ["rm", "--force"])

    def test_inconsistent_hashes_counts_or_incomplete_passing_results_are_refused(self):
        for change in ({"candidate_sha256": "0" * 64}, {"predecessor_sha256": "0" * 64},
                       {"verifier_sha256": "0" * 64}, {"passed": 999},
                       {"checks": [], "total": 0, "passed": 0}):
            with self.subTest(change=change):
                self.candidate.write_text("# synthetic candidate snapshot\n")
                self.payload = change
                with self.assertRaises(container.ContainerError):
                    self.evaluate()

    def test_unconfirmed_boundaries_are_refused(self):
        for change in ({"uid": 0}, {"interfaces": ["eth0", "lo"]}, {"root_read_only": False},
                       {"input_read_only": False}, {"memory_bytes": "max"}, {"pids": 999},
                       {"docker_socket_present": True}, {"host_sentinel_present": True},
                       {"cpu_quota": ["max", "100000"]}):
            with self.subTest(change=change):
                self.candidate.write_text("# synthetic candidate snapshot\n")
                self.boundary_change = change
                with self.assertRaises(container.ContainerError):
                    self.evaluate()

    def test_execution_timeout_is_bounded_before_contacting_docker(self):
        for value in (0, 301, -1, True):
            with self.subTest(timeout=value), patch("phoenix.container.subprocess.run") as run:
                with self.assertRaises(container.ContainerError):
                    container.evaluate_in_container(self.candidate, timeout=value)
                run.assert_not_called()

    def test_cli_reports_missing_runtime_with_exit_two(self):
        errors = io.StringIO()
        with patch.object(sys, "argv", ["phoenix", "container-verify"]), \
                patch("phoenix.container.shutil.which", return_value=None), \
                patch.object(cli, "evaluate") as local, contextlib.redirect_stderr(errors):
            self.assertEqual(cli.main(), 2)
        local.assert_not_called()
        self.assertIn("did not run", errors.getvalue())


if __name__ == "__main__":
    unittest.main()
