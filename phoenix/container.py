"""Optional Docker acceptance using current or verified archived source snapshots."""
import hashlib
import json
from pathlib import Path
import re
import secrets
import shutil
import subprocess
import tempfile

from .records import ROOT


# Official Python multi-platform manifest, resolved from Docker Hub on 2026-10-23.
IMAGE = "python:3.12-slim-bookworm@sha256:6a0e06f5ba0b373d051aabd3fffb9b873f27f9a2a3a14bcbe37dee6f0e3bbc70"
POLICY = {"memory_bytes": 268435456, "swap_bytes": 0, "cpus": 1, "pids": 128,
          "tmpfs_bytes": 67108864, "uid": 65534, "network": "none"}

# The unchanged HTTP verifier and its subject share this container's loopback.
# Only source snapshots are mounted; the repository, home and Docker socket are not.
ENTRYPOINT = '''import hashlib,json,os
from pathlib import Path
import types

def mount_flags(target):
    for line in Path('/proc/mounts').read_text().splitlines():
        fields = line.split()
        if fields[1] == target:
            return fields[3].split(',')
    return []

status = dict(line.split(':', 1) for line in Path('/proc/self/status').read_text().splitlines() if ':' in line)
cpu = Path('/sys/fs/cgroup/cpu.max').read_text().split()
probe = {
    'uid': os.getuid(),
    'interfaces': sorted(p.name for p in Path('/sys/class/net').iterdir()),
    'root_read_only': 'ro' in mount_flags('/'),
    'input_read_only': 'ro' in mount_flags('/input'),
    'tmpfs_bytes': os.statvfs('/tmp').f_frsize * os.statvfs('/tmp').f_blocks,
    'tmpfs_restrictions': all(flag in mount_flags('/tmp') for flag in ('nosuid', 'nodev', 'noexec')),
    'effective_capabilities': int(status['CapEff'].strip(), 16),
    'no_new_privileges': status['NoNewPrivs'].strip() == '1',
    'memory_bytes': int(Path('/sys/fs/cgroup/memory.max').read_text()),
    'swap_bytes': int(Path('/sys/fs/cgroup/memory.swap.max').read_text()),
    'pids': int(Path('/sys/fs/cgroup/pids.max').read_text()),
    'cpu_quota': cpu,
    'docker_socket_present': Path('/var/run/docker.sock').exists(),
    'host_sentinel_present': 'PHOENIX_HOST_SENTINEL' in os.environ,
    'input_files': sorted(p.name for p in Path('/input').iterdir()),
}
assert probe['uid'] == 65534 and probe['interfaces'] == ['lo']
assert probe['root_read_only'] and probe['input_read_only']
assert probe['tmpfs_bytes'] == 67108864 and probe['tmpfs_restrictions']
assert probe['effective_capabilities'] == 0 and probe['no_new_privileges']
assert probe['memory_bytes'] == 268435456 and probe['swap_bytes'] == 0 and probe['pids'] == 128
assert cpu[0] != 'max' and int(cpu[0]) == int(cpu[1])
assert not probe['docker_socket_present'] and not probe['host_sentinel_present']
expected = ['entrypoint.py', 'server.py', 'verifier.py']
if Path('/input/predecessor.py').exists():
    expected.append('predecessor.py')
assert probe['input_files'] == sorted(expected)
verifier_bytes = Path('/input/verifier.py').read_bytes()
module = types.ModuleType('container_verifier')
exec(compile(verifier_bytes, 'container-verifier.py', 'exec'), module.__dict__)
kwargs = {'predecessor': '/input/predecessor.py'} if Path('/input/predecessor.py').exists() else {}
report = module.evaluate('/input/server.py', **kwargs)
report['container_probe'] = probe
report['verifier_sha256'] = hashlib.sha256(verifier_bytes).hexdigest()
print(json.dumps(report))
'''


class ContainerError(RuntimeError):
    pass


def _keep_error(completed):
    directory = ROOT / ".phoenix/container-errors"
    directory.mkdir(parents=True, exist_ok=True)
    # Docker errors may name local paths. Keep diagnostics out of public evidence.
    (directory / "latest.log").write_text(completed.stderr + completed.stdout, encoding="utf-8")


def _image_id(executable):
    command = [executable, "image", "inspect", IMAGE, "--format", "{{.Id}}"]
    inspected = subprocess.run(command, capture_output=True, text=True, timeout=15)
    if inspected.returncode:
        pulled = subprocess.run([executable, "pull", IMAGE], capture_output=True, text=True, timeout=120)
        if pulled.returncode:
            _keep_error(pulled)
            raise ContainerError("Could not pull the pinned image; check the Docker daemon and registry access")
        inspected = subprocess.run(command, capture_output=True, text=True, timeout=15)
    if inspected.returncode or re.fullmatch(r"sha256:[0-9a-f]{64}", inspected.stdout.strip()) is None:
        raise ContainerError("Could not inspect the pinned Docker image")
    return inspected.stdout.strip()


def _validate(report, sources):
    if not isinstance(report, dict) or report.get("status") not in ("passed", "failed"):
        raise ContainerError("Container did not return an acceptance report")
    bindings = {"server.py": "candidate_sha256", "verifier.py": "verifier_sha256"}
    if "predecessor.py" in sources:
        bindings["predecessor.py"] = "predecessor_sha256"
    for name, field in bindings.items():
        if report.get(field) != hashlib.sha256(sources[name]).hexdigest():
            raise ContainerError("Container report does not match its input snapshot: " + name)
    checks = report.get("checks")
    if not isinstance(checks, list) or any(not isinstance(c, dict) or type(c.get("passed")) is not bool
                                           or not isinstance(c.get("id"), str) for c in checks):
        raise ContainerError("Invalid container check results")
    if type(report.get("passed")) is not int or type(report.get("total")) is not int:
        raise ContainerError("Invalid container check counts")
    if report["passed"] != sum(c["passed"] for c in checks) or report["total"] != len(checks):
        raise ContainerError("Inconsistent container check counts")
    from .audit import CHECK_IDS
    if report["status"] == "passed" and not (
        len(checks) == len(CHECK_IDS) and report["passed"] == len(CHECK_IDS)
        and {c.get("id") for c in checks} == CHECK_IDS
    ):
        raise ContainerError("Incomplete passing container verdict")
    probe = report.get("container_probe", {})
    expected_files = sorted(sources)
    expected = {"uid": 65534, "interfaces": ["lo"], "root_read_only": True,
                "input_read_only": True, "effective_capabilities": 0, "no_new_privileges": True,
                "tmpfs_bytes": POLICY["tmpfs_bytes"], "tmpfs_restrictions": True,
                "memory_bytes": POLICY["memory_bytes"], "swap_bytes": 0, "pids": 128,
                "docker_socket_present": False, "host_sentinel_present": False,
                "input_files": expected_files}
    if not isinstance(probe, dict) or any(probe.get(key) != value for key, value in expected.items()):
        raise ContainerError("Container boundary probe did not confirm the required policy")
    try:
        quota, period = probe["cpu_quota"]
        if int(quota) != int(period) or int(period) <= 0:
            raise ValueError()
    except (KeyError, TypeError, ValueError):
        raise ContainerError("Container CPU limit was not confirmed")


def evaluate_in_container(candidate, predecessor=None, timeout=90):
    """Return a separate verification result; never fall back to host execution."""
    return evaluate_snapshot_in_container(
        Path(candidate).read_bytes(), (ROOT / "phoenix/verify.py").read_bytes(),
        predecessor_source=Path(predecessor).read_bytes() if predecessor else None,
        timeout=timeout,
    )


def evaluate_snapshot_in_container(candidate_source, verifier_source, predecessor_source=None, timeout=90):
    """Stage the supplied bytes without loading archived code on the host."""
    executable = shutil.which("docker")
    if executable is None:
        raise ContainerError("Docker is not installed; container verification did not run")
    if type(timeout) is not int or not 1 <= timeout <= 300:
        raise ContainerError("Container timeout must be an integer from 1 to 300 seconds")
    sources = {"server.py": candidate_source, "verifier.py": verifier_source,
               "entrypoint.py": ENTRYPOINT.encode("utf-8")}
    if predecessor_source is not None:
        sources["predecessor.py"] = predecessor_source
    if any(not isinstance(source, bytes) for source in sources.values()):
        raise ContainerError("Container inputs must be frozen source bytes")
    try:
        image_id = _image_id(executable)
    except subprocess.TimeoutExpired as error:
        raise ContainerError("Docker image preparation exceeded its time limit; no container was started") from error
    except OSError as error:
        raise ContainerError("Docker could not be executed; no container was started") from error
    try:
        with tempfile.TemporaryDirectory(prefix="phoenix-container-") as temporary:
            stage = Path(temporary)
            stage.chmod(0o755)
            if "," in str(stage):
                raise ContainerError("Docker staging path cannot contain a comma")
            for name, source in sources.items():
                path = stage / name
                path.write_bytes(source)
                path.chmod(0o644)
            name = "phoenix-verify-" + secrets.token_hex(8)
            command = [executable, "run", "--rm", "--name", name, "--pull", "never",
                       "--network", "none", "--read-only", "--user", "65534:65534",
                       "--cap-drop", "ALL", "--security-opt", "no-new-privileges=true",
                       "--cgroupns", "private", "--memory", "256m", "--memory-swap", "256m",
                       "--cpus", "1", "--pids-limit", "128", "--ulimit", "nofile=256:256",
                       "--tmpfs", "/tmp:rw,nosuid,nodev,noexec,size=64m,mode=1777",
                       "--mount", "type=bind,source=%s,target=/input,readonly" % stage,
                       "--workdir", "/tmp", "--env", "HOME=/tmp", "--env", "TMPDIR=/tmp",
                       "--env", "PYTHONDONTWRITEBYTECODE=1", "--env", "PYTHONUNBUFFERED=1",
                       IMAGE, "python", "-I", "-B", "/input/entrypoint.py"]
            try:
                completed = subprocess.run(command, capture_output=True, text=True, timeout=timeout)
                if completed.returncode:
                    _keep_error(completed)
                    raise ContainerError("Container exited with code %s; inspect local .phoenix/container-errors/latest.log" % completed.returncode)
                try:
                    report = json.loads(completed.stdout)
                except ValueError as error:
                    raise ContainerError("Container returned invalid JSON") from error
                _validate(report, sources)
            finally:
                # Remove this invocation's container, including its entire process tree.
                # It may already have been removed by --rm after a normal exit.
                subprocess.run([executable, "rm", "--force", name], capture_output=True, text=True, timeout=10)
    except subprocess.TimeoutExpired as error:
        raise ContainerError("Docker operation exceeded its time limit; container cleanup was requested") from error
    except OSError as error:
        raise ContainerError("Docker could not be executed; container verification did not complete") from error
    report.update(execution="docker-container", evaluation_isolation="container; cooperative evaluator",
                  container_image=IMAGE, container_image_id=image_id, container_policy=dict(POLICY),
                  container_entrypoint_sha256=hashlib.sha256(sources["entrypoint.py"]).hexdigest())
    return report
