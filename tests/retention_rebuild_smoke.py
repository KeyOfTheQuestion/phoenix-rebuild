"""Synthetic v2 CLI adapter + actual acceptance/replay; never call a model."""
import contextlib
import io
import json
from pathlib import Path
import shutil
import sys
import tempfile
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from phoenix.audit import audit, verified_run
from phoenix.container import RETENTION_SUPPORT, evaluate_snapshot_in_container
from phoenix.rebuild import rebuild
from phoenix.records import ROOT


def check_retention_adapter(use_container=False):
    ledger = (ROOT / "docs/runs.json").read_bytes()
    row = next(r for r in json.loads(ledger) if r["kind"] == "ai-rebuild" and r["status"] == "passed"
               and r.get("contract_id", "001") == "001")
    which = shutil.which
    with tempfile.TemporaryDirectory(prefix="phoenix-v2-adapter-ci-") as temporary:
        root = Path(temporary)
        (root / "specs").mkdir()
        (root / "phoenix").mkdir()
        for name in ("verify.py", "retention.py", "records.py", "audit.py", "empty_candidate.py"):
            shutil.copyfile(ROOT / "phoenix" / name, root / "phoenix" / name)
        shutil.copyfile(ROOT / "specs/webhook-inbox-retention.md", root / "specs/webhook-inbox-retention.md")
        previous = root / "previous.py"
        previous.write_bytes((ROOT / "docs/evidence" / row["id"] / "server.py").read_bytes())
        fixture = root / "synthetic-codex"
        fixture.write_text("#!" + sys.executable + "\n" + '''import json,sys
from pathlib import Path
if sys.argv[1:]==['--version']:
 print('synthetic-v2-ci-fixture (no model call)')
 raise SystemExit(0)
assert '--ignore-user-config' in sys.argv and '--model' not in sys.argv
workspace=Path(sys.argv[sys.argv.index('--cd')+1])
assert {p.name for p in workspace.iterdir()}=={'contract.md','response-schema.json'}
sys.stdin.read()
Path(sys.argv[sys.argv.index('--output-last-message')+1]).write_text(json.dumps({'server_py':%r}))
print(json.dumps({'type':'turn.completed','usage':None}))
''' % (ROOT / "examples/retention/server.py").read_text())
        fixture.chmod(0o755)
        with patch("phoenix.rebuild.ROOT", root), patch("phoenix.records.ROOT", root), \
                patch("phoenix.rebuild.shutil.which", side_effect=lambda name: str(fixture) if name == "codex" else which(name)), \
                contextlib.redirect_stdout(io.StringIO()):
            report, = rebuild(attempts=1, contract_id="002", predecessor=previous)
        assert report["status"] == "passed" and report["passed"] == report["total"] == 32
        assert report["kind"] == "ai-rebuild" and report["verifier_profile"] == "retention-ai-v1"
        assert report["provenance"]["version"] == "synthetic-v2-ci-fixture (no model call)"
        assert audit(root / "docs") == []
        _, artifacts = verified_run(root / "docs", report["id"])
        result = report
        if use_container:
            result = evaluate_snapshot_in_container(artifacts["server.py"], artifacts["verifier.py"],
                predecessor_source=artifacts["predecessor.py"], verifier_profile="retention-ai-v1",
                support_sources={name: artifacts[name] for name in RETENTION_SUPPORT})
            assert result["status"] == "passed" and result["passed"] == result["total"] == 32
            assert result["kind"] == "ai-rebuild" and result["verifier_profile"] == "retention-ai-v1"
            for field in ("candidate_sha256", "predecessor_sha256", "contract_sha256", "verifier_sha256",
                          "shared_subject_sha256", "retention_verifier_sha256", "records_support_sha256", "audit_support_sha256"):
                assert result[field] == report[field], field
        assert (ROOT / "docs/runs.json").read_bytes() == ledger
        return {"generation": "synthetic CLI returning the existing reference; no model call",
                "temporary_ledger_audit": "passed; discarded after the control", "public_ledger_changed": False,
                "source_predecessor_record": row["id"], "verification": result}


if __name__ == "__main__":
    print("RETENTION_ADAPTER_EVIDENCE=" + json.dumps(check_retention_adapter(), sort_keys=True))
