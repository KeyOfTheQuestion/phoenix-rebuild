"""An integrity check, not a signature or third-party attestation."""
import json
from pathlib import Path
from .records import sha


def audit(site):
    site = Path(site)
    errors = []
    ledger = json.loads((site / "runs.json").read_text())
    seen = set()
    for summary in ledger:
        run_id = summary["id"]
        if run_id in seen or Path(run_id).name != run_id:
            errors.append("Duplicate or unsafe run id")
            continue
        seen.add(run_id)
        directory = site / "evidence" / run_id
        try:
            report = json.loads((directory / "report.json").read_text())
            manifest = json.loads((directory / "manifest.json").read_text())
            if not {"server.py", "contract.md", "report.json"} <= set(manifest):
                errors.append(run_id + ": missing required manifest entries")
            for name, digest in manifest.items():
                if Path(name).name != name or sha(directory / name) != digest:
                    errors.append(run_id + ": artifact digest mismatch: " + name)
            for key, value in summary.items():
                if report.get(key) != value:
                    errors.append(run_id + ": ledger differs from report: " + key)
            if sha(directory / "server.py") != report.get("candidate_sha256"):
                errors.append(run_id + ": candidate digest mismatch")
            if sha(directory / "contract.md") != report.get("contract_sha256"):
                errors.append(run_id + ": contract digest mismatch")
            if (directory / "verifier.py").exists() and sha(directory / "verifier.py") != report.get("verifier_sha256"):
                errors.append(run_id + ": verifier digest mismatch")
            if report.get("predecessor_sha256") and sha(directory / "predecessor.py") != report["predecessor_sha256"]:
                errors.append(run_id + ": predecessor digest mismatch")
            checks = report["checks"]
            if report["passed"] != sum(c["passed"] for c in checks) or report["total"] != len(checks):
                errors.append(run_id + ": incorrect check counts")
            if report["status"] == "passed" and not (len(checks) == 19 and all(c["passed"] for c in checks)):
                errors.append(run_id + ": invalid passing verdict")
            if report["status"] == "passed" and {c["id"] for c in checks} != {
                "C01", "C02", "C03", "C04", "C05", "C06", "C07", "C08",
                "S01", "S02", "S03", "S04", "S05", "R01", "R02", "R03", "R04", "D01", "D02"}:
                errors.append(run_id + ": passing verdict has incorrect check identifiers")
        except (OSError, KeyError, TypeError, ValueError) as error:
            errors.append(run_id + ": incomplete evidence bundle (" + type(error).__name__ + ")")
    return errors
