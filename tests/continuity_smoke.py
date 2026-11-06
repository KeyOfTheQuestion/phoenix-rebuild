"""Exercise the actual continuity commands and frozen bundles without model calls."""
import json
from pathlib import Path
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from phoenix.continuity import BINDINGS, replay_continuity, verify_continuity
from phoenix.records import ROOT


def main():
    ledger = (ROOT / "docs/runs.json").read_bytes()
    reference = ROOT / "examples/retention/server.py"
    with tempfile.TemporaryDirectory(prefix="phoenix-continuity-smoke-") as temporary:
        bundle = Path(temporary) / "reference"
        initial = verify_continuity(reference, reference, bundle)
        repeated = replay_continuity(bundle)
        assert initial["passed"] == initial["total"] == repeated["passed"] == repeated["total"] == 8
        assert initial["status"] == repeated["status"] == "passed"
        assert all(initial[field] == repeated[field] for field in BINDINGS.values())
    observations = []
    for manifest in sorted((ROOT / "docs/maintenance").glob("*-continuity*/manifest.json")):
        bundle = manifest.parent
        before = {p.name: p.read_bytes() for p in bundle.iterdir()}
        archived = json.loads(before["report.json"])
        fresh = replay_continuity(bundle)
        # Replay validates complete passes and truthful failed prefixes. Retain each
        # fresh verdict: publishing a failure must not become a CI infrastructure failure.
        assert all(archived[field] == fresh[field] for field in BINDINGS.values())
        assert {p.name: p.read_bytes() for p in bundle.iterdir()} == before
        observations.append({"bundle": bundle.name, "archived_status": archived["status"], "report": fresh})
    assert observations, "At least one actual frozen handoff bundle must be checked"
    assert (ROOT / "docs/runs.json").read_bytes() == ledger
    print("CONTINUITY_EVIDENCE=" + json.dumps(
        {"reference": initial, "reference_replay": repeated, "observations": observations,
         "public_ledger_changed": False, "scope": "actual HTTP handoff and verified frozen replay; no model call"},
        sort_keys=True))


if __name__ == "__main__":
    main()
