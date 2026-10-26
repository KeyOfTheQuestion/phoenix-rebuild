"""Real Docker integration checks. Run explicitly on a Linux cgroup-v2 Docker host."""
import contextlib
from datetime import datetime, timezone
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from phoenix.audit import audit, verified_run
from phoenix.container import ContainerError, evaluate_in_container
from phoenix.records import ROOT
from phoenix.rebuild import rebuild


def check_rebuild_adapter(record):
    """Synthetic CLI fixture + real Docker; never call a model or publish a run."""
    original, artifacts = verified_run(ROOT / "docs", record["id"])
    public_ledger = (ROOT / "docs/runs.json").read_bytes()
    which = shutil.which
    with tempfile.TemporaryDirectory(prefix="phoenix-rebuild-ci-") as temporary:
        root = Path(temporary)
        (root / "specs").mkdir()
        (root / "phoenix").mkdir()
        (root / "docs").mkdir()
        (root / "specs/webhook-inbox.md").write_bytes(artifacts["contract.md"])
        (root / "phoenix/verify.py").write_bytes(artifacts["verifier.py"])
        previous = root / "previous.py"
        previous.write_bytes(artifacts["predecessor.py"])
        counter = root / "fixture-calls.txt"
        fixture = root / "synthetic-codex"
        fixture.write_text("#!" + sys.executable + "\n" + '''import json,sys
from pathlib import Path
if sys.argv[1:] == ['--version']:
 print('synthetic-ci-fixture (no model call)')
 raise SystemExit(0)
assert '--ignore-user-config' in sys.argv and '--model' not in sys.argv
workspace = Path(sys.argv[sys.argv.index('--cd') + 1])
assert {p.name for p in workspace.iterdir()} == {'contract.md', 'response-schema.json'}
sys.stdin.read()
counter = Path(%r)
with counter.open('a') as output:
 output.write('synthetic generation\\n')
answer = Path(sys.argv[sys.argv.index('--output-last-message') + 1])
answer.write_text(json.dumps({'server_py': %r}))
print(json.dumps({'type': 'turn.completed', 'usage': None}))
''' % (str(counter), artifacts["server.py"].decode("utf-8")))
        fixture.chmod(0o755)
        with patch("phoenix.rebuild.ROOT", root), patch("phoenix.records.ROOT", root), \
                patch("phoenix.rebuild.shutil.which", side_effect=lambda name: str(fixture) if name == "codex" else which(name)), \
                contextlib.redirect_stdout(io.StringIO()):
            report, = rebuild(attempts=1, predecessor=previous, use_container=True)
            assert report["status"] == "passed" and report["passed"] == report["total"] == 19
            assert report["execution"] == "docker-container"
            assert report["provenance"]["version"] == "synthetic-ci-fixture (no model call)"
            assert report["provenance"]["requested_verification"] == "docker-container"
            for field in ("candidate_sha256", "verifier_sha256", "predecessor_sha256", "contract_sha256"):
                assert report[field] == original[field], field
            assert audit(root / "docs") == []
            before = len(counter.read_text().splitlines())
            with patch("phoenix.container.evaluate_snapshot_in_container", side_effect=ContainerError("synthetic runtime failure control")):
                failure, = rebuild(attempts=2, predecessor=previous, use_container=True)
            assert len(counter.read_text().splitlines()) == before + 1
            assert failure["status"] == "verification-error" and failure["passed"] == failure["total"] == 0
            retained = root / "docs/evidence" / failure["id"] / "server.py"
            assert retained.read_bytes() == artifacts["server.py"]
            assert audit(root / "docs") == []
        assert (ROOT / "docs/runs.json").read_bytes() == public_ledger
        return {"generation": "synthetic CLI fixture returning an existing archive; no model call",
                "source_record_id": record["id"], "public_ledger_changed": False,
                "temporary_ledger_audit": "passed; discarded after this control",
                "verification": {key: value for key, value in report.items()
                                 if key not in ("id", "kind", "provenance", "human_interventions", "usage", "cost_usd")},
                "runtime_failure_control": {"method": "synthetic ContainerError after fixture generation",
                                            "status": failure["status"], "passed": 0, "total": 0,
                                            "generation_calls": 1,
                                            "retained_source_sha256": hashlib.sha256(retained.read_bytes()).hexdigest()}}


def main():
    reference = ROOT / "examples/reference/server.py"
    ledger = json.loads((ROOT / "docs/runs.json").read_text())
    row = next(r for r in ledger if r["kind"] == "ai-rebuild" and r["status"] == "passed")
    archived, _ = verified_run(ROOT / "docs", row["id"])

    def replay_in_container(record):
        original, _ = verified_run(ROOT / "docs", record["id"])
        completed = subprocess.run([sys.executable, "-m", "phoenix", "replay", record["id"], "--container"],
                                   capture_output=True, text=True, timeout=180)
        assert completed.returncode == 0, completed.stderr
        report = json.loads(completed.stdout)
        assert report["passed"] == report["total"] == 19, report
        assert report["replay_source"] == "verified-archive-bytes"
        assert report["replay_run_id"] == record["id"]
        for field in ("candidate_sha256", "verifier_sha256", "predecessor_sha256", "contract_sha256"):
            assert report.get(field) == original.get(field), field
        return report

    with patch.dict(os.environ, {"PHOENIX_HOST_SENTINEL": "synthetic-host-only"}):
        baseline = evaluate_in_container(reference, predecessor=reference)
        assert baseline["passed"] == baseline["total"] == 19, baseline
        ai = replay_in_container(row)
        legacy_row = next(r for r in ledger if r["kind"] == "ai-rebuild" and r["status"] == "passed"
                          and not r.get("predecessor_sha256"))
        legacy = replay_in_container(legacy_row)
        assert legacy["verifier_sha256"] != baseline["verifier_sha256"]
        adapter_control = check_rebuild_adapter(row)
        with tempfile.TemporaryDirectory(prefix="phoenix-container-ci-") as temporary:
            directory = Path(temporary)
            candidate = directory / "server.py"
            assert ai["candidate_sha256"] == archived["candidate_sha256"]
            assert ai["predecessor_sha256"] == archived["predecessor_sha256"]
            candidate.write_text("import time\ntime.sleep(60)\n")
            try:
                evaluate_in_container(candidate, timeout=2)
            except ContainerError as error:
                assert "time limit" in str(error), error
            else:
                raise AssertionError("The intentionally slow candidate did not hit its container deadline")
    remaining = subprocess.run([shutil.which("docker"), "ps", "--all", "--filter", "name=^/phoenix-verify-",
                                "--format", "{{.Names}}"], capture_output=True, text=True, timeout=10)
    assert remaining.returncode == 0 and not remaining.stdout.strip(), "A Phoenix test container was left behind"
    print("CONTAINER_EVIDENCE=" + json.dumps({
        "schema_version": 1, "checked_at": datetime.now(timezone.utc).isoformat(),
        "archived_run_id": row["id"], "reference": baseline, "archived_ai": ai,
        "legacy_sql_fixture_run_id": legacy_row["id"], "legacy_sql_fixture": legacy,
        "rebuild_adapter_control": adapter_control,
        "timeout_control": "deadline refused the slow candidate; no Phoenix containers remained",
    }, sort_keys=True))


if __name__ == "__main__":
    try:
        main()
    except (ContainerError, AssertionError):
        # CI fixtures are public synthetic inputs; expose their runtime diagnostics.
        log = ROOT / ".phoenix/container-errors/latest.log"
        if os.environ.get("GITHUB_ACTIONS") == "true" and log.exists():
            print(log.read_text(), file=sys.stderr)
        raise
