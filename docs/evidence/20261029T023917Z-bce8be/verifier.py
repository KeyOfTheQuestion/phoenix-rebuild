"""Trusted local replay of a frozen contract-002 verifier package."""
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
