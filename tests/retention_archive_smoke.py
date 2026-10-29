"""Replay a real published reference bundle in CI; never call a model."""
import json
from pathlib import Path
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from phoenix.audit import verified_run
from phoenix.records import ROOT


def main():
    rows = json.loads((ROOT / "docs/runs.json").read_text())
    row = next(row for row in rows if row.get("contract_id") == "002" and row["status"] == "passed")
    report, artifacts = verified_run(ROOT / "docs", row["id"])
    directory = ROOT / "docs/evidence" / row["id"]
    before = {p.name: p.read_bytes() for p in directory.iterdir() if p.is_file()}
    names = sorted(p.name for p in directory.iterdir())
    completed = subprocess.run([sys.executable, "-m", "phoenix", "replay", row["id"]],
                               check=True, capture_output=True, text=True, timeout=60)
    result = json.loads(completed.stdout)
    assert result["status"] == "passed" and result["passed"] == result["total"] == 32
    assert result["kind"] == "retention-reference-verification" and result["contract_id"] == "002"
    assert result["replay_source"] == "verified-archive-bytes" and result["replay_run_id"] == row["id"]
    for field in ("candidate_sha256", "predecessor_sha256", "contract_sha256", "verifier_sha256",
                  "shared_subject_sha256", "retention_verifier_sha256", "records_support_sha256", "audit_support_sha256"):
        assert result[field] == report[field], field
    assert {p.name: p.read_bytes() for p in directory.iterdir() if p.is_file()} == before
    assert sorted(p.name for p in directory.iterdir()) == names
    assert set(artifacts) == set(before) - {"manifest.json"}
    print("RETENTION_ARCHIVE_EVIDENCE=" + json.dumps({"run_id": row["id"], "scope": "frozen contract-002 reference replay; no model call",
          "archive_unchanged": True, "result": result}, sort_keys=True))


if __name__ == "__main__":
    main()
