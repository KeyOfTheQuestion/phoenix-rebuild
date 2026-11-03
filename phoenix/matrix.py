"""Bounded local replay across explicitly selected, already-installed runtimes."""
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import secrets
import shutil
import signal
import subprocess
import tempfile

from .audit import CHECK_IDS, RETENTION_CHECK_IDS, EvidenceError, valid_execution_runtime


WORKER = '''import hashlib,json,platform,sqlite3,types
from pathlib import Path
root=Path(__file__).parent
verifier=root/'verifier.py'
module=types.ModuleType('matrix_archived_verifier')
module.__file__=str(verifier)
exec(compile(verifier.read_bytes(),str(verifier),'exec'),module.__dict__)
kwargs={'predecessor':root/'predecessor.py'} if (root/'predecessor.py').exists() else {}
report=module.evaluate(root/'server.py',**kwargs)
report['execution_runtime']={'python_implementation':platform.python_implementation(),
    'python_version':platform.python_version(),'sqlite_version':sqlite3.sqlite_version}
report['verifier_sha256']=hashlib.sha256(verifier.read_bytes()).hexdigest()
report['contract_sha256']=hashlib.sha256((root/'contract.md').read_bytes()).hexdigest()
print(json.dumps(report))
'''


def _validate(report, archived, sources):
    if not isinstance(report, dict) or report.get("status") not in ("passed", "failed") \
            or not valid_execution_runtime(report.get("execution_runtime")):
        raise ValueError("Invalid runtime or verdict")
    bindings = {"server.py": "candidate_sha256", "verifier.py": "verifier_sha256", "contract.md": "contract_sha256"}
    if "predecessor.py" in sources:
        bindings["predecessor.py"] = "predecessor_sha256"
    expected = CHECK_IDS
    if archived.get("contract_id", "001") == "001" and "expected_schema_version" in report and (
        type(report["expected_schema_version"]) is not int or report["expected_schema_version"] != 1
    ):
        raise ValueError("Invalid baseline schema expectation")
    if archived.get("contract_id") == "002":
        expected = RETENTION_CHECK_IDS
        bindings.update({"baseline.py": "shared_subject_sha256", "retention.py": "retention_verifier_sha256",
                         "records.py": "records_support_sha256", "audit.py": "audit_support_sha256"})
        for key in ("kind", "contract_id", "contract_version", "verifier_profile", "expected_total",
                    "baseline_expected_schema_version"):
            if type(report.get(key)) is not type(archived[key]) or report[key] != archived[key]:
                raise ValueError("Invalid retained contract profile")
    if any(report.get(field) != hashlib.sha256(sources[name]).hexdigest() for name, field in bindings.items()):
        raise ValueError("Runtime report source mismatch")
    checks = report.get("checks")
    if not isinstance(checks, list) or not checks or any(not isinstance(c, dict) or type(c.get("passed")) is not bool
                                         or not isinstance(c.get("id"), str) for c in checks):
        raise ValueError("Invalid checks")
    identifiers = [c["id"] for c in checks]
    if len(set(identifiers)) != len(identifiers) or not set(identifiers) <= expected \
            or type(report.get("passed")) is not int or type(report.get("total")) is not int \
            or report["passed"] != sum(c["passed"] for c in checks) or report["total"] != len(checks):
        raise ValueError("Invalid check coverage/counts")
    if report["status"] == "passed" and (set(identifiers) != expected or report["passed"] != len(expected)):
        raise ValueError("Incomplete passing verdict")


def _execute(executable, directory, timeout):
    environment = {key: os.environ[key] for key in ("PATH", "SYSTEMROOT", "TMPDIR", "LANG") if key in os.environ}
    command = [executable, "-I", "-B", str(directory / "matrix-entrypoint.py")]
    output = errors = ""
    try:
        process = subprocess.Popen(command, cwd=directory, env=environment, start_new_session=True,
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                                   encoding="utf-8", errors="replace")
    except OSError as error:
        return "runtime-start-error", "", str(error)
    try:
        output, errors = process.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        # Subject children inherit this runtime's new process group. Terminate
        # only this invocation, then force cleanup even if the worker exits first.
        for sig, deadline in ((signal.SIGTERM, 1), (signal.SIGKILL, 5)):
            try:
                os.killpg(process.pid, sig)
            except ProcessLookupError:
                pass
            try:
                output, errors = process.communicate(timeout=deadline)
            except subprocess.TimeoutExpired:
                continue
        return "timeout", output, errors
    return ("process-exit" if process.returncode else None), output, errors


def replay_matrix(site, run_id, interpreters, timeout=90):
    """Audit once, stage the same captured bytes per runtime; never call a model."""
    if os.name != "posix":
        raise EvidenceError("Runtime matrix requires POSIX process-group cleanup")
    if not isinstance(interpreters, (list, tuple)) or not 1 <= len(interpreters) <= 4 \
            or any(not isinstance(value, str) or not value for value in interpreters):
        raise EvidenceError("Select between one and four installed Python executables")
    if type(timeout) is not int or not 1 <= timeout <= 300:
        raise EvidenceError("Each runtime timeout must be an integer from 1 to 300 seconds")
    from .audit import verified_run
    archived, artifacts = verified_run(site, run_id)
    if archived["status"] == "generation-failed" or "verifier.py" not in artifacts:
        raise EvidenceError("This record has no executable candidate or archived verifier")
    names = {"server.py", "verifier.py", "contract.md"}
    if archived.get("predecessor_sha256"):
        names.add("predecessor.py")
    if archived.get("contract_id") == "002":
        names.update(("baseline.py", "retention.py", "records.py", "audit.py"))
    sources = {name: artifacts[name] for name in names}
    matrix = {"schema_version": 1, "kind": "archive-runtime-matrix", "source_run_id": run_id,
              "started_at": datetime.now(timezone.utc).isoformat(), "execution": "local-runtime-matrix",
              "scope": "verified frozen archive; explicit installed runtimes; no model call or ledger publication",
              "timeout_per_runtime_seconds": timeout,
              "matrix_worker_sha256": hashlib.sha256(WORKER.encode("utf-8")).hexdigest(),
              "source_sha256": {name: hashlib.sha256(raw).hexdigest() for name, raw in sorted(sources.items())},
              "results": []}
    for index, requested in enumerate(interpreters, 1):
        result = {"runtime_index": index, "status": "runtime-error"}
        executable = shutil.which(requested)
        if executable is None:
            result["error"] = "interpreter-not-found"
        else:
            with tempfile.TemporaryDirectory(prefix="phoenix-runtime-matrix-") as temporary:
                directory = Path(temporary)
                for name, raw in sources.items():
                    (directory / name).write_bytes(raw)
                (directory / "matrix-entrypoint.py").write_text(WORKER, encoding="utf-8")
                error, output, errors = _execute(os.path.abspath(executable), directory, timeout)
                if error is None:
                    try:
                        if len(output) > 1024 * 1024:
                            raise ValueError("Report too large")
                        report = json.loads(output)
                        _validate(report, archived, sources)
                        result.update(status=report["status"], report=report)
                    except (ValueError, TypeError, KeyError):
                        error = "invalid-report"
                if error:
                    result["error"] = error
                    # Private diagnostics can name executable/staging paths.
                    logs = Path(site).parent / ".phoenix/matrix-errors"
                    logs.mkdir(parents=True, exist_ok=True)
                    (logs / (secrets.token_hex(8) + ".log")).write_text(errors + "\n" + output, encoding="utf-8")
        matrix["results"].append(result)
    matrix["total"] = len(matrix["results"])
    matrix["passed"] = sum(r["status"] == "passed" for r in matrix["results"])
    matrix["status"] = "runtime-error" if any(r["status"] == "runtime-error" for r in matrix["results"]) \
        else "passed" if matrix["passed"] == matrix["total"] else "failed"
    return matrix
