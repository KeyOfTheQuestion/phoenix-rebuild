"""Inspect real loopback delivery with an archived AI inbox and independent receivers."""
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import time
import unittest
from datetime import datetime, timezone

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from phoenix.records import ROOT
from phoenix.verify import runtime_identity
from test_delivery import DeliveryTests
from test_delivery_journal import JournalTests
from test_delivery_policy import PolicyTests


class EvidenceResult(unittest.TestResult):
    def __init__(self):
        super().__init__()
        self.checks = []

    def startTest(self, test):
        super().startTest(test)
        self.started = time.monotonic()
        self.failed = False

    def addFailure(self, test, error):
        self.failed = True
        super().addFailure(test, error)

    def addError(self, test, error):
        self.failed = True
        super().addError(test, error)

    def addSubTest(self, test, subtest, error):
        if error is not None:
            self.failed = True
        super().addSubTest(test, subtest, error)

    def stopTest(self, test):
        name = test._testMethodName
        self.checks.append({"id": name.split("_")[1], "title": name.split("_", 2)[2].replace("_", " "),
                            "passed": not self.failed, "duration_ms": round((time.monotonic() - self.started) * 1000, 2)})
        super().stopTest(test)


def main():
    started_at = datetime.now(timezone.utc).isoformat()
    ledger = (ROOT / "docs/runs.json").read_bytes()
    chosen = next(r for r in json.loads(ledger) if r["kind"] == "ai-rebuild" and r["status"] == "passed"
                  and r.get("contract_id") == "002")
    paths = {"inbox.py": ROOT / "docs/evidence" / chosen["id"] / "server.py",
             "delivery.py": ROOT / "phoenix/delivery.py", "http_helper.py": ROOT / "phoenix/verify.py",
             "checks.py": ROOT / "tests/test_delivery.py", "runner.py": Path(__file__),
             "cli.py": ROOT / "phoenix/__main__.py", "records.py": ROOT / "phoenix/records.py",
             "journal.py": ROOT / "phoenix/delivery_journal.py", "journal_checks.py": ROOT / "tests/test_delivery_journal.py",
             "policy.py": ROOT / "phoenix/delivery_policy.py", "policy_checks.py": ROOT / "tests/test_delivery_policy.py"}
    hashes = {name: hashlib.sha256(path.read_bytes()).hexdigest() for name, path in paths.items()}
    result = EvidenceResult()
    with tempfile.TemporaryDirectory(prefix="phoenix-delivery-smoke-") as temporary:
        candidate = Path(temporary) / "inbox.py"
        candidate.write_bytes(paths["inbox.py"].read_bytes())
        previous = DeliveryTests.backend
        DeliveryTests.backend = candidate
        try:
            unittest.defaultTestLoader.loadTestsFromTestCase(DeliveryTests).run(result)
            unittest.defaultTestLoader.loadTestsFromTestCase(JournalTests).run(result)
            unittest.defaultTestLoader.loadTestsFromTestCase(PolicyTests).run(result)
        finally:
            DeliveryTests.backend = previous
    assert (ROOT / "docs/runs.json").read_bytes() == ledger
    assert all(hashlib.sha256(path.read_bytes()).hexdigest() == hashes[name] for name, path in paths.items())
    report = {"schema_version": 1, "kind": "loopback-delivery-verification",
              "started_at": started_at, "execution_runtime": runtime_identity(),
              "inbox_ai_run_id": chosen["id"], "source_sha256": hashes, "checks": result.checks,
              "passed": sum(c["passed"] for c in result.checks), "total": len(result.checks),
              "status": "passed" if result.wasSuccessful() and len(result.checks) == 30 else "failed",
              "scope": "current checkout; source HTTP, loopback POST, protocol faults, local SQLite journal and captured destination policy; synthetic data",
              "public_ledger_changed": False}
    print("DELIVERY_EVIDENCE=" + json.dumps(report, sort_keys=True))
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
