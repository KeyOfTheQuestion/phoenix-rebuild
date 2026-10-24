"""Real Docker integration checks. Run explicitly on a Linux cgroup-v2 Docker host."""
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from phoenix.audit import verified_run
from phoenix.container import ContainerError, evaluate_in_container
from phoenix.records import ROOT


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
