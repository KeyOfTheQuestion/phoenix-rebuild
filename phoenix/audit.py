"""Check and snapshot evidence before use; hashes are not an attestation."""
import hashlib
import json
from pathlib import Path
import re


CHECK_IDS = {
    "C01", "C02", "C03", "C04", "C05", "C06", "C07", "C08",
    "S01", "S02", "S03", "S04", "S05", "R01", "R02", "R03", "R04", "D01", "D02",
}


class EvidenceError(ValueError):
    """The requested record cannot be verified as a complete published bundle."""


def valid_run_id(value):
    return isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]*", value) is not None


def read_ledger(site):
    try:
        ledger = json.loads((Path(site) / "runs.json").read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise EvidenceError("Cannot read the public ledger: " + type(error).__name__) from error
    if not isinstance(ledger, list):
        raise EvidenceError("The public ledger must be a list")
    seen = set()
    for row in ledger:
        if not isinstance(row, dict) or not valid_run_id(row.get("id")):
            raise EvidenceError("Invalid or unsafe run id in the public ledger")
        if row["id"] in seen:
            raise EvidenceError("Duplicate run id in the public ledger: " + row["id"])
        seen.add(row["id"])
    return ledger


def inspect_bundle(directory, summary):
    """Return checked bytes, so replay need not reopen mutable source files."""
    directory = Path(directory)
    if directory.is_symlink() or not directory.is_dir():
        raise EvidenceError("Missing run directory or unsupported directory symlink")

    def read_artifact(name):
        if not isinstance(name, str) or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", name) is None:
            raise EvidenceError("Unsafe artifact name")
        path = directory / name
        if path.is_symlink() or not path.is_file():
            raise EvidenceError("Missing artifact or unsupported symlink: " + name)
        return path.read_bytes()

    try:
        manifest = json.loads(read_artifact("manifest.json"))
        if not isinstance(manifest, dict):
            raise EvidenceError("Manifest must be an object")
        if not {"server.py", "contract.md", "report.json"} <= set(manifest):
            raise EvidenceError("Missing required manifest entries")
        artifacts = {}
        for name, expected in manifest.items():
            if not isinstance(expected, str) or re.fullmatch(r"[0-9a-f]{64}", expected) is None:
                raise EvidenceError("Invalid artifact digest")
            raw = read_artifact(name)
            if hashlib.sha256(raw).hexdigest() != expected:
                raise EvidenceError("Artifact digest mismatch: " + name)
            artifacts[name] = raw
        # Derived __pycache__ directories from older replay versions are ignored;
        # no replay code is imported from them or written into the archive.
        files = {p.name for p in directory.iterdir() if p.is_file() or p.is_symlink()} - {"manifest.json"}
        if files != set(manifest):
            raise EvidenceError("Unlisted or missing top-level artifacts")
        report = json.loads(artifacts["report.json"])
        if not isinstance(report, dict):
            raise EvidenceError("Report must be an object")
        for key, value in summary.items():
            if report.get(key) != value:
                raise EvidenceError("ledger differs from report: " + key)
        bound = {"server.py": "candidate_sha256", "contract.md": "contract_sha256"}
        if report.get("verifier_sha256"):
            bound["verifier.py"] = "verifier_sha256"
        if report.get("predecessor_sha256"):
            bound["predecessor.py"] = "predecessor_sha256"
        for filename, field in bound.items():
            if filename not in artifacts or hashlib.sha256(artifacts[filename]).hexdigest() != report.get(field):
                raise EvidenceError("Report digest mismatch or missing artifact: " + filename)
        checks = report["checks"]
        if not isinstance(checks, list) or any(not isinstance(c, dict) or type(c.get("passed")) is not bool for c in checks):
            raise EvidenceError("Invalid check results")
        if type(report["passed"]) is not int or type(report["total"]) is not int:
            raise EvidenceError("Check counts must be integers")
        if report["passed"] != sum(c["passed"] for c in checks) or report["total"] != len(checks):
            raise EvidenceError("Incorrect check counts")
        if report["status"] not in ("passed", "failed", "generation-failed", "verification-error"):
            raise EvidenceError("Unknown verdict")
        if report["status"] == "verification-error" and not (
            checks == [] and report["passed"] == report["total"] == 0
            and report.get("execution") == "docker-container-attempted"
            and isinstance(report.get("error"), str) and report["error"]
        ):
            raise EvidenceError("Invalid verification error; it must not claim an acceptance verdict")
        if report["status"] == "passed" and not (
            len(checks) == len(CHECK_IDS) and all(c["passed"] for c in checks)
            and {c.get("id") for c in checks} == CHECK_IDS
        ):
            raise EvidenceError("Invalid passing verdict or check identifiers")
    except EvidenceError:
        raise
    except (OSError, KeyError, TypeError, ValueError) as error:
        raise EvidenceError("Incomplete evidence bundle: " + type(error).__name__) from error
    return report, artifacts


def verified_run(site, run_id):
    if not valid_run_id(run_id):
        raise EvidenceError("run_id must be a published record id")
    row = next((r for r in read_ledger(site) if r["id"] == run_id), None)
    if row is None:
        raise EvidenceError("Run is not present in the public ledger: " + run_id)
    return inspect_bundle(Path(site) / "evidence" / run_id, row)


def audit(site):
    site = Path(site)
    try:
        ledger = read_ledger(site)
    except EvidenceError as error:
        return [str(error)]
    errors = []
    for summary in ledger:
        try:
            inspect_bundle(site / "evidence" / summary["id"], summary)
        except EvidenceError as error:
            errors.append(summary["id"] + ": " + str(error))
    return errors
