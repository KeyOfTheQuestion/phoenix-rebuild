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
    archived, artifacts = verified_run(ROOT / "docs", row["id"])
    with patch.dict(os.environ, {"PHOENIX_HOST_SENTINEL": "synthetic-host-only"}):
        baseline = evaluate_in_container(reference, predecessor=reference)
        assert baseline["passed"] == baseline["total"] == 19, baseline
        with tempfile.TemporaryDirectory(prefix="phoenix-container-ci-") as temporary:
            directory = Path(temporary)
            candidate = directory / "server.py"
            previous = directory / "predecessor.py"
            candidate.write_bytes(artifacts["server.py"])
            previous.write_bytes(artifacts["predecessor.py"])
            ai = evaluate_in_container(candidate, predecessor=previous)
            assert ai["passed"] == ai["total"] == 19, ai
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
        "timeout_control": "deadline refused the slow candidate; no Phoenix containers remained",
    }, sort_keys=True))


if __name__ == "__main__":
    try:
        main()
    except ContainerError:
        # CI fixtures are public synthetic inputs; expose their runtime diagnostics.
        log = ROOT / ".phoenix/container-errors/latest.log"
        if os.environ.get("GITHUB_ACTIONS") == "true" and log.exists():
            print(log.read_text(), file=sys.stderr)
        raise
