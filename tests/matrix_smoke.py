"""Actual archive matrix replay in each CI interpreter; no generation or publication."""
import json
from pathlib import Path
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from phoenix.audit import verified_run
from phoenix.records import ROOT


def main():
    ledger = (ROOT / "docs/runs.json").read_bytes()
    rows = json.loads(ledger)
    chosen = [next(r for r in rows if r["kind"] == "ai-rebuild" and r["status"] == "passed"
                   and r.get("contract_id", "001") == "001"),
              next(r for r in rows if r.get("contract_id") == "002" and r["status"] == "passed"
                   and r["kind"] == "retention-reference-verification")]
    v2_ai = next((r for r in rows if r.get("contract_id") == "002" and r["kind"] == "ai-rebuild"
                  and r["status"] == "passed"), None)
    if v2_ai:
        chosen.append(v2_ai)
    observations = []
    for row in chosen:
        archived, artifacts = verified_run(ROOT / "docs", row["id"])
        directory = ROOT / "docs/evidence" / row["id"]
        before = {p.name: p.read_bytes() for p in directory.iterdir() if p.is_file()}
        completed = subprocess.run([sys.executable, "-m", "phoenix", "replay-matrix", row["id"],
                                    "--python", sys.executable, "--timeout", "30"],
                                   capture_output=True, text=True, timeout=45)
        assert completed.returncode == 0, completed.stderr + completed.stdout
        matrix = json.loads(completed.stdout)
        assert (matrix["status"], matrix["passed"], matrix["total"]) == ("passed", 1, 1)
        report = matrix["results"][0]["report"]
        assert report["passed"] == report["total"] == (32 if row.get("contract_id") == "002" else 19)
        assert report["candidate_sha256"] == archived["candidate_sha256"]
        assert report["execution_runtime"]["python_version"] == ".".join(map(str, sys.version_info[:3]))
        import hashlib
        for name, digest in matrix["source_sha256"].items():
            assert digest == hashlib.sha256(artifacts[name]).hexdigest(), name
        assert before == {p.name: p.read_bytes() for p in directory.iterdir() if p.is_file()}
        observations.append(matrix)
    assert ledger == (ROOT / "docs/runs.json").read_bytes()
    print("RUNTIME_MATRIX_EVIDENCE=" + json.dumps({"scope": "actual frozen v1/v2 archive replay; no model call",
                                                 "archives_and_ledger_unchanged": True, "observations": observations}))


if __name__ == "__main__":
    main()
