"""Freeze and publish contract-002 reference checks; no model or live-code replay."""
from pathlib import Path
import tempfile
import types

from .records import ROOT, publish

# This entrypoint and every imported project module are archived as source bytes.
ENTRYPOINT = '''"""Trusted local replay of a frozen contract-002 verifier package."""
import hashlib
import importlib
from pathlib import Path
import secrets
import sys
import tempfile
import types


def evaluate(candidate, predecessor=None):
    if predecessor is None:
        raise RuntimeError("Contract-002 replay needs its recorded predecessor")
    archive = Path(__file__).parent
    sources = {name: (archive / name).read_bytes() for name in
               ("baseline.py", "retention.py", "records.py", "audit.py", "contract.md")}
    name = "_phoenix_retention_archive_" + secrets.token_hex(8)
    with tempfile.TemporaryDirectory(prefix="phoenix-frozen-retention-") as temporary:
        root = Path(temporary)
        package = root / "phoenix"
        package.mkdir()
        (root / "specs").mkdir()
        for artifact, module in (("baseline.py", "verify.py"), ("retention.py", "retention.py"),
                                 ("records.py", "records.py"), ("audit.py", "audit.py")):
            (package / module).write_bytes(sources[artifact])
        (root / "specs/webhook-inbox-retention.md").write_bytes(sources["contract.md"])
        namespace = types.ModuleType(name)
        namespace.__path__ = [str(package)]
        namespace.__package__ = name
        sys.modules[name] = namespace
        try:
            verifier = importlib.import_module(name + ".retention")
            report = verifier.evaluate_retention_full(candidate, predecessor)
        finally:
            for key in list(sys.modules):
                if key == name or key.startswith(name + "."):
                    del sys.modules[key]
    report["retention_verifier_sha256"] = report["verifier_sha256"]
    report["verifier_sha256"] = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    report["records_support_sha256"] = hashlib.sha256(sources["records.py"]).hexdigest()
    report["audit_support_sha256"] = hashlib.sha256(sources["audit.py"]).hexdigest()
    report["verifier_profile"] = "retention-composed-v1"
    return report
'''


def replay_frozen_retention(artifacts):
    """Use only bytes returned by verified_run, never reopen the original archive."""
    with tempfile.TemporaryDirectory(prefix="phoenix-retention-replay-") as temporary:
        directory = Path(temporary)
        for name, raw in artifacts.items():
            (directory / name).write_bytes(raw)
        verifier = directory / "verifier.py"
        module = types.ModuleType("frozen_retention_entrypoint")
        module.__file__ = str(verifier)
        exec(compile(artifacts["verifier.py"], str(verifier), "exec"), module.__dict__)
        return module.evaluate(directory / "server.py", predecessor=directory / "predecessor.py")


def publish_retention(candidate, predecessor):
    """Capture source first, evaluate those snapshots, then publish the same bytes."""
    artifacts = {
        "server.py": Path(candidate).read_bytes(),
        "predecessor.py": Path(predecessor).read_bytes(),
        "verifier.py": ENTRYPOINT.encode("utf-8"),
        "contract.md": (ROOT / "specs/webhook-inbox-retention.md").read_bytes(),
        "baseline.py": (ROOT / "phoenix/verify.py").read_bytes(),
        "retention.py": (ROOT / "phoenix/retention.py").read_bytes(),
        "records.py": (ROOT / "phoenix/records.py").read_bytes(),
        "audit.py": (ROOT / "phoenix/audit.py").read_bytes(),
    }
    report = replay_frozen_retention(artifacts)
    # Publishing also reads only captured source copies, after evaluation has finished.
    with tempfile.TemporaryDirectory(prefix="phoenix-retention-publish-") as temporary:
        directory = Path(temporary)
        candidate_path = directory / "server.py"
        previous_path = directory / "predecessor.py"
        candidate_path.write_bytes(artifacts["server.py"])
        previous_path.write_bytes(artifacts["predecessor.py"])
        return publish(report, candidate_path, predecessor=previous_path,
                       verifier_source=artifacts["verifier.py"], contract_source=artifacts["contract.md"],
                       provenance={"generator": "reference-verification", "model": None,
                                   "verification": "frozen contract-002 composed source package; no model call"},
                       extra_artifacts={name: artifacts[name] for name in ("baseline.py", "retention.py", "records.py", "audit.py")})
