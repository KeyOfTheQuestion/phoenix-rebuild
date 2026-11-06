"""Run and replay a separate v2 handoff observation from captured source bytes."""
import hashlib
import json
from pathlib import Path
import tempfile
import types

from .audit import EvidenceError, valid_execution_runtime
from .records import ROOT


BINDINGS = {
    "server.py": "candidate_sha256",
    "predecessor.py": "predecessor_sha256",
    "contract.md": "contract_sha256",
    "verifier.py": "verifier_sha256",
    "baseline.py": "shared_subject_sha256",
}
PROFILE = {
    "schema_version": 1,
    "kind": "v2-continuity-verification",
    "contract_id": "002",
    "contract_version": "2.0.0",
    "verifier_profile": "v2-continuity-v1",
    "execution": "local-subprocess",
    "expected_total": 8,
}
CHECK_IDS = tuple("V%02d" % i for i in range(1, 9))
ARTIFACTS = set(BINDINGS) | {"report.json"}


def digest(data):
    return hashlib.sha256(data).hexdigest()


def json_bytes(obj):
    return (json.dumps(obj, indent=2, ensure_ascii=False) + "\n").encode("utf-8")


def validate_report(report, sources):
    if not isinstance(report, dict) or any(
        type(report.get(key)) is not type(value) or report[key] != value for key, value in PROFILE.items()
    ):
        raise EvidenceError("Invalid continuity report profile")
    for name, field in BINDINGS.items():
        if report.get(field) != digest(sources[name]):
            raise EvidenceError("Missing or inconsistent continuity report binding for " + name)
    if not valid_execution_runtime(report.get("execution_runtime")):
        raise EvidenceError("Missing continuity execution runtime")
    checks = report.get("checks")
    if not isinstance(checks, list) or not 1 <= len(checks) <= len(CHECK_IDS):
        raise EvidenceError("Invalid continuity check count")
    for check, expected in zip(checks, CHECK_IDS):
        if not isinstance(check, dict) or check.get("id") != expected or type(check.get("passed")) is not bool:
            raise EvidenceError("Invalid continuity check sequence")
    passed = sum(check["passed"] for check in checks)
    full_pass = passed == len(CHECK_IDS)
    stopped_at_failure = passed == len(checks) - 1 and not checks[-1]["passed"]
    if not (full_pass or stopped_at_failure) or report.get("status") != ("passed" if full_pass else "failed"):
        raise EvidenceError("Inconsistent continuity verdict")
    if type(report.get("passed")) is not int or report["passed"] != passed \
            or type(report.get("total")) is not int or report["total"] != len(checks):
        raise EvidenceError("Incorrect continuity report totals")


def load_module(name, raw, path):
    module = types.ModuleType(name)
    module.__file__ = str(path)
    exec(compile(raw, str(path), "exec"), module.__dict__)
    return module


def run_captured(sources):
    with tempfile.TemporaryDirectory(prefix="phoenix-v2-handoff-") as temporary:
        directory = Path(temporary)
        for name, data in sources.items():
            (directory / name).write_bytes(data)
        client = load_module("phoenix_frozen_subject", sources["baseline.py"], directory / "baseline.py")
        verifier = load_module("phoenix_frozen_continuity", sources["verifier.py"], directory / "verifier.py")
        report = verifier.evaluate(directory, client)
    report.update(PROFILE)
    report.update({field: digest(sources[name]) for name, field in BINDINGS.items()})
    report["fixture"] = ("The v2 predecessor creates policies, inboxes and keyed events via HTTP. "
                         "The fixture backdates only synthetic event timestamps in SQLite while stopped "
                         "and observes the predecessor again before handoff.")
    validate_report(report, sources)
    return report


def verify_continuity(candidate, predecessor, output_dir=None):
    destination = Path(output_dir) if output_dir is not None else None
    if destination is not None and (destination.exists() or destination.is_symlink()):
        raise EvidenceError("Continuity output requires a new directory")
    paths = {"server.py": Path(candidate), "predecessor.py": Path(predecessor),
             "contract.md": ROOT / "specs/webhook-inbox-retention.md",
             "verifier.py": ROOT / "phoenix/continuity_fixture.py", "baseline.py": ROOT / "phoenix/verify.py"}
    sources = {name: path.read_bytes() for name, path in paths.items()}
    report = run_captured(sources)
    if destination is not None:
        artifacts = dict(sources, **{"report.json": json_bytes(report)})
        artifacts["manifest.json"] = json_bytes({name: digest(data) for name, data in sorted(artifacts.items())})
        # Reserve a fresh name exclusively; install all finished files as one directory.
        destination.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=destination.parent, prefix=".phoenix-continuity-") as temporary:
            staging = Path(temporary) / "bundle"
            staging.mkdir()
            for name, raw in artifacts.items():
                (staging / name).write_bytes(raw)
            destination.mkdir()
            try:
                staging.replace(destination)
            except OSError:
                destination.rmdir()
                raise
    return report


def replay_continuity(directory):
    directory = Path(directory)
    if directory.is_symlink() or not directory.is_dir():
        raise EvidenceError("Continuity bundle must be a regular directory")
    entries = {p.name: p for p in directory.iterdir()}
    if set(entries) != ARTIFACTS | {"manifest.json"} or any(
        path.is_symlink() or not path.is_file() for path in entries.values()
    ):
        raise EvidenceError("Continuity bundle has missing or unexpected artifacts")
    artifacts = {name: path.read_bytes() for name, path in entries.items()}
    try:
        manifest = json.loads(artifacts.pop("manifest.json"))
        stored = json.loads(artifacts["report.json"])
    except (ValueError, UnicodeError) as error:
        raise EvidenceError("Invalid continuity manifest or report JSON") from error
    if manifest != {name: digest(raw) for name, raw in artifacts.items()}:
        raise EvidenceError("Continuity manifest digest mismatch")
    sources = {name: artifacts[name] for name in BINDINGS}
    validate_report(stored, sources)
    return run_captured(sources)
