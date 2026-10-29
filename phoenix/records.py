"""Publish small, inspectable evidence bundles. Never publish provider logs."""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import secrets
import re

ROOT = Path(__file__).resolve().parent.parent


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def publish(report, candidate, prompt=None, provenance=None, predecessor=None, verifier_source=None, contract_source=None,
            extra_artifacts=None):
    extra_artifacts = extra_artifacts or {}
    reserved = {"server.py", "verifier.py", "predecessor.py", "contract.md", "prompt.md", "report.json", "manifest.json"}
    for name, raw in extra_artifacts.items():
        if not isinstance(name, str) or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", name) is None \
                or name in reserved or not isinstance(raw, bytes):
            raise ValueError("Extra artifacts require safe, non-reserved filenames and frozen bytes")
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + secrets.token_hex(3)
    dest = ROOT / "docs" / "evidence" / run_id
    dest.mkdir(parents=True)
    source = Path(candidate).read_bytes()
    (dest / "server.py").write_bytes(source)
    verifier_source = verifier_source if verifier_source is not None else (ROOT / "phoenix" / "verify.py").read_bytes()
    (dest / "verifier.py").write_bytes(verifier_source)
    if predecessor:
        (dest / "predecessor.py").write_bytes(Path(predecessor).read_bytes())
    contract = ROOT / "specs" / "webhook-inbox.md"
    contract_source = contract_source if contract_source is not None else contract.read_bytes()
    (dest / "contract.md").write_bytes(contract_source)
    if prompt:
        (dest / "prompt.md").write_text(prompt, encoding="utf-8")
    for name, raw in extra_artifacts.items():
        (dest / name).write_bytes(raw)
    report = dict(report, id=run_id, candidate_sha256=hashlib.sha256(source).hexdigest(), contract_sha256=hashlib.sha256(contract_source).hexdigest(),
                  verifier_sha256=hashlib.sha256(verifier_source).hexdigest(),
                  provenance=provenance or {"generator": "human-reviewed-reference", "model": None},
                  usage=(provenance or {}).get("usage"), cost_usd=None,
                  contract_id=report.get("contract_id", "001"),
                  contract_version=report.get("contract_version", "1.0.0"),
                  verifier_profile=report.get("verifier_profile", "contract-001"))
    (dest / "report.json").write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    manifest = {p.name: sha(p) for p in sorted(dest.iterdir()) if p.is_file()}
    (dest / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    index_path = ROOT / "docs" / "runs.json"
    index = json.loads(index_path.read_text()) if index_path.exists() else []
    summary = {key: report.get(key) for key in (
        "id", "kind", "status", "started_at", "passed", "total", "duration_seconds",
        "candidate_sha256", "human_interventions", "usage", "cost_usd", "provenance",
        "predecessor_sha256", "compatibility_fixture", "generation_seconds",
        "contract_id", "contract_version", "verifier_profile")}
    index.insert(0, summary)
    index_path.write_text(json.dumps(index, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return report
