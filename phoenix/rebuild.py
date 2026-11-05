"""A real generator adapter. The reference implementation is never copied in."""
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import time
import types

from .records import ROOT, publish

OUTPUT_SCHEMA = {
    "type": "object", "properties": {"server_py": {"type": "string"}},
    "required": ["server_py"], "additionalProperties": False,
}


def rebuild(attempts=2, timeout=300, model=None, use_user_config=False, predecessor=None,
            use_container=False, verification_timeout=90, contract_id="001"):
    if contract_id not in ("001", "002"):
        raise RuntimeError("Select contract 001 or 002")
    if use_container:
        from .container import ContainerError, RETENTION_SUPPORT, evaluate_snapshot_in_container, prepare_container
        prepare_container(verification_timeout)
    executable = shutil.which("codex")
    if executable is None:
        raise RuntimeError("Install and authenticate the Codex CLI first; see docs/setup.md")
    version = subprocess.run([executable, "--version"], capture_output=True, text=True, timeout=10).stdout.strip()
    frozen_sources = None
    if contract_id == "002":
        from .retention_archive import capture_retention_sources
        frozen_sources = capture_retention_sources(ROOT, ai=True)
        specification = frozen_sources["contract.md"].decode("utf-8")
        verifier_source = frozen_sources["verifier.py"]
    else:
        specification = (ROOT / "specs" / "webhook-inbox.md").read_text(encoding="utf-8")
        verifier_source = (ROOT / "phoenix" / "verify.py").read_bytes()
    predecessor_path = Path(predecessor) if predecessor else ROOT / "examples" / "reference" / "server.py"
    predecessor_source = predecessor_path.read_bytes()
    if not use_container and contract_id == "001":
        frozen_verifier = types.ModuleType("phoenix_frozen_verifier")
        exec(compile(verifier_source, "phoenix-verifier-snapshot.py", "exec"), frozen_verifier.__dict__)

    def record(report, candidate, prompt, provenance, predecessor=None, candidate_source=None):
        if frozen_sources is not None:
            # Failure bundles retain the same full input closure, even if no
            # candidate could be executed. Temporary copies publish frozen bytes.
            report = dict(report, kind="ai-rebuild", human_interventions=0,
                          contract_id="002", contract_version="2.0.0", verifier_profile="retention-ai-v1",
                          baseline_expected_schema_version=2, expected_total=32,
                          predecessor_sha256=hashlib.sha256(predecessor_source).hexdigest())
            for name, field in (("baseline.py", "shared_subject_sha256"), ("retention.py", "retention_verifier_sha256"),
                                ("records.py", "records_support_sha256"), ("audit.py", "audit_support_sha256")):
                report[field] = hashlib.sha256(frozen_sources[name]).hexdigest()
            with tempfile.TemporaryDirectory(prefix="phoenix-v2-evidence-") as temporary:
                directory = Path(temporary)
                current = directory / "server.py"
                current.write_bytes(candidate_source if candidate_source is not None else Path(candidate).read_bytes())
                previous = directory / "predecessor.py"
                previous.write_bytes(predecessor_source)
                return publish(report, current, prompt, provenance, predecessor=previous,
                               verifier_source=verifier_source, contract_source=frozen_sources["contract.md"],
                               extra_artifacts={name: frozen_sources[name] for name in
                                                ("baseline.py", "retention.py", "records.py", "audit.py")})
        return publish(report, candidate, prompt, provenance, predecessor=predecessor,
                       verifier_source=verifier_source, contract_source=specification.encode("utf-8"))
    feedback = ""
    reports = []
    for attempt in range(1, attempts + 1):
        # A new directory on every attempt. No reference source, past implementation,
        # repository history, verifier, or test results are staged here.
        with tempfile.TemporaryDirectory(prefix="phoenix-generate-") as tmp:
            workspace = Path(tmp)
            (workspace / "contract.md").write_text(specification, encoding="utf-8")
            schema = workspace / "response-schema.json"
            schema.write_text(json.dumps(OUTPUT_SCHEMA))
            answer = workspace / "answer.json"
            prompt = (
                "Implement the complete HTTP service specified below from scratch. "
                "Return the complete Python source in server_py, using the requested JSON schema. "
                "No markdown fences. Do not run tools, read other files, consult a reference "
                "implementation, or inspect any tests. Your only implementation input is this "
                "public contract. The result will be executed against independent HTTP checks.\n\n"
                + specification + feedback
            )
            command = [executable, "exec", "--skip-git-repo-check", "--ephemeral",
                       "--sandbox", "read-only", "--json", "--color", "never",
                       "--output-schema", str(schema), "--output-last-message", str(answer),
                       "--cd", str(workspace)]
            if not use_user_config:
                command += ["--ignore-user-config"]
            if model:
                command += ["--model", model]
            command += ["-"]
            print("Generating from the contract: attempt %s/%s" % (attempt, attempts), flush=True)
            provenance = {"generator": "codex-cli", "version": version, "model": model,
                          "model_selection": "explicit" if model else ("local CLI configuration" if use_user_config else "clean CLI default"),
                          "user_configuration_loaded": use_user_config,
                          "attempt": attempt, "usage": None,
                          "requested_verification": "docker-container" if use_container else "local-subprocess"}
            if use_container:
                provenance["verification_timeout_seconds"] = verification_timeout
            start = time.monotonic()
            try:
                completed = subprocess.run(command, input=prompt, capture_output=True, text=True, timeout=timeout)
            except subprocess.TimeoutExpired:
                report = generation_error("Generation exceeded its %ss time limit" % timeout, start)
                reports.append(record(report, ROOT / "phoenix" / "empty_candidate.py", prompt,
                                       provenance))
                break
            usage = None
            tool_calls = 0
            for line in completed.stdout.splitlines():
                try:
                    event = json.loads(line)
                    if event.get("type") == "turn.completed":
                        usage = event.get("usage")
                    if event.get("type") == "item.completed" and event.get("item", {}).get("type") in (
                        "command_execution", "mcp_tool_call", "web_search"):
                        tool_calls += 1
                except ValueError:
                    continue
            provenance.update(usage=usage, observed_tool_calls=tool_calls,
                              generation_seconds=round(time.monotonic() - start, 3),
                              context_boundary="fresh-directory, prompt-enforced; not a hostile-agent sandbox")
            if completed.returncode or not answer.exists():
                # Raw stderr can contain machine paths and account context. Keep it local.
                logs = ROOT / ".phoenix" / "generator-errors"
                logs.mkdir(parents=True, exist_ok=True)
                (logs / "latest.log").write_text(completed.stderr, encoding="utf-8")
                report = generation_error("Generator exited with code %s; inspect local .phoenix/generator-errors/latest.log" % completed.returncode, start)
                reports.append(record(report, ROOT / "phoenix" / "empty_candidate.py", prompt, provenance))
                break
            code = None
            candidate = workspace / "server.py"
            try:
                payload = json.loads(answer.read_text())
                code = payload["server_py"]
                if not isinstance(code, str) or not code.strip():
                    raise ValueError("Missing Python source")
                compile(code, "server.py", "exec")
            except (ValueError, KeyError, TypeError, SyntaxError) as error:
                report = generation_error("Invalid generated Python: " + type(error).__name__, start)
                if isinstance(code, str):
                    candidate.write_text(code, encoding="utf-8")
                reports.append(record(report, candidate if candidate.exists() else ROOT / "phoenix" / "empty_candidate.py", prompt, provenance))
                feedback = "\n\nPrevious generation returned invalid Python. Return a complete, valid program."
                continue
            candidate.write_text(code, encoding="utf-8")
            # Stage the predecessor only after the generator has exited.
            predecessor = workspace / "predecessor.py"
            predecessor.write_bytes(predecessor_source)
            candidate_source = candidate.read_bytes()
            if use_container:
                verification_start = time.monotonic()
                try:
                    options = {} if frozen_sources is None else {
                        "verifier_profile": "retention-ai-v1",
                        "support_sources": {name: frozen_sources[name] for name in RETENTION_SUPPORT}}
                    report = evaluate_snapshot_in_container(
                        candidate_source, verifier_source, predecessor_source=predecessor_source,
                        timeout=verification_timeout, **options,
                    )
                    report.update(kind="ai-rebuild", human_interventions=0)
                except (ContainerError, OSError) as error:
                    # A runtime failure is not acceptance feedback. Retain the generated
                    # source and stop instead of spending another model attempt.
                    report = verification_error(verification_start, predecessor_source,
                                                str(error) if isinstance(error, ContainerError) else type(error).__name__)
            elif frozen_sources is not None:
                from .retention_archive import replay_frozen_retention
                verification_start = time.monotonic()
                try:
                    report = replay_frozen_retention(dict(frozen_sources, **{
                        "server.py": candidate_source, "predecessor.py": predecessor_source}))
                except Exception as error:
                    logs = ROOT / ".phoenix/generator-errors"
                    logs.mkdir(parents=True, exist_ok=True)
                    (logs / "latest-verification.log").write_text(str(error), encoding="utf-8")
                    report = verification_error(verification_start, predecessor_source, type(error).__name__,
                                                execution="local-subprocess-attempted")
            else:
                report = frozen_verifier.evaluate(candidate, kind="ai-rebuild", predecessor=predecessor)
            report["generation_seconds"] = provenance["generation_seconds"]
            published = record(report, candidate, prompt, provenance, predecessor=predecessor,
                               candidate_source=candidate_source)
            reports.append(published)
            print("%s: %s/%s checks — %s" % (published["id"], published["passed"], published["total"], published["status"]), flush=True)
            if report["status"] == "passed":
                break
            if report["status"] == "verification-error":
                break
            failed = [{"id": c["id"], "title": c["title"], "error": c.get("error")} for c in report["checks"] if not c["passed"]]
            feedback = "\n\nA previous independent attempt failed these checks. Reimplement from the contract, addressing this feedback:\n" + json.dumps(failed)
    return reports


def verification_error(start, predecessor_source, reason, execution="docker-container-attempted"):
    from datetime import datetime, timezone
    return {"schema_version": 1, "kind": "ai-rebuild", "status": "verification-error",
            "started_at": datetime.now(timezone.utc).isoformat(), "passed": 0, "total": 0,
            "duration_seconds": round(time.monotonic() - start, 3), "human_interventions": 0,
            "execution": execution, "checks": [],
            "predecessor_sha256": hashlib.sha256(predecessor_source).hexdigest(),
            "error": ("Container" if execution == "docker-container-attempted" else "Local") + " verification could not produce a validated report (" + reason + "); no acceptance verdict. "
                     + ("No host fallback or generation retry. Check Docker and local .phoenix/container-errors/latest.log when present."
                        if execution == "docker-container-attempted" else
                        "No verification-mode switch or generation retry. Check local .phoenix/generator-errors/latest-verification.log.")}


def generation_error(message, start):
    from datetime import datetime, timezone
    return {"schema_version": 1, "kind": "ai-rebuild", "status": "generation-failed",
            "started_at": datetime.now(timezone.utc).isoformat(), "passed": 0, "total": 0,
            "duration_seconds": round(time.monotonic() - start, 3), "human_interventions": 0,
            "execution": "not-executed", "checks": [], "error": message}
