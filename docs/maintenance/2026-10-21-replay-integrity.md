# 2026-10-21: verify the evidence before replay

Archived replay could execute a changed verifier or candidate even though its
published manifest no longer matched. Replay now checks the complete bundle
first, then runs the exact bytes it checked. The historical archives were not
edited. This completes a reliability improvement to the roadmap's archived-replay
capability; container isolation remains pending.

## Minimal reproduction and root cause

Run the following from the repository root after this repair. It loads the old
CLI from the launch commit and compares it with the repaired CLI. Each case uses
a disposable copy of an archive; no published file is modified. The injected
verifier only writes a marker file and then continues normal verification.

```sh
python3 - <<'PY'
import contextlib
import io
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import types
from unittest.mock import patch

root = Path.cwd()
run_id = "20261020T091931Z-90cdc2"
old_source = subprocess.check_output([
    "git", "show", "2cbddfc:phoenix/__main__.py"
], text=True)
for label, source in (("Before fix", old_source),
                      ("After fix", (root / "phoenix/__main__.py").read_text())):
    with tempfile.TemporaryDirectory() as tmp:
        scratch = Path(tmp)
        archive = scratch / "docs/evidence" / run_id
        shutil.copytree(root / "docs/evidence" / run_id, archive,
                        ignore=shutil.ignore_patterns("__pycache__"))
        row = next(r for r in json.loads((root / "docs/runs.json").read_text())
                   if r["id"] == run_id)
        (scratch / "docs/runs.json").write_text(json.dumps([row]))
        marker = scratch / "unverified-code-ran"
        verifier = archive / "verifier.py"
        verifier.write_text("from pathlib import Path\nPath(%r).write_text('executed')\n"
                            % str(marker) + verifier.read_text())
        module = types.ModuleType("phoenix.replay_reproduction")
        module.__package__ = "phoenix"
        exec(compile(source, "replay-cli.py", "exec"), module.__dict__)
        module.ROOT = scratch
        output, errors = io.StringIO(), io.StringIO()
        with patch.object(sys, "argv", ["phoenix", "replay", run_id]), \
                contextlib.redirect_stdout(output), contextlib.redirect_stderr(errors):
            result = module.main()
        print(label + ": replay exit code = " + str(result))
        print(label + ": modified verifier executed = " + str(marker.exists()))
        if errors.getvalue():
            print(errors.getvalue().strip())
PY
```

Actual output:

```text
Before fix: replay exit code = 0
Before fix: modified verifier executed = True
After fix: replay exit code = 2
After fix: modified verifier executed = False
Replay refused: Artifact digest mismatch: verifier.py
```

In the [original `phoenix/__main__.py`, lines 53–67](https://github.com/KeyOfTheQuestion/phoenix-rebuild/blob/2cbddfc40c23fdfeef0718c9225ce1b16fef1aca/phoenix/__main__.py#L53-L67),
lines 61–63 imported and executed `verifier.py` before any evidence validation;
lines 65–67 used the candidate and predecessor directly from disk. Integrity
checking existed only as a separate `audit` command (lines 46–52). A user could
therefore receive a passing replay result from altered source.

## Repair and scope

- `phoenix.audit.verified_run` shares bundle validation with the audit command.
  It checks the ledger, manifest, actual file set, report bindings, hashes,
  verdict and check counts before returning verified bytes.
- Replay refuses inconsistent bundles, unsafe/unlisted record IDs, symlinked
  artifacts and generation failures with a controlled exit code 2.
- The verifier is compiled from checked bytes without a disk import or bytecode
  cache. The candidate and recorded predecessor are copied from checked bytes
  into a temporary directory. Files changed after inspection cannot replace
  those checked bytes. Replay does not write into the archive.
- Valid archives retain their recorded verifier and predecessor. The service
  contract, current HTTP verifier and previous outcomes are unchanged.

These checks establish consistency with the local manifest. They do not prove
that code is safe, sign the evidence or protect against a maintainer replacing
both code and hashes. Archived code still runs with local user permissions.
Old `__pycache__` subdirectories may be present from earlier replay versions;
they are ignored and never imported by the repaired replay.

<details><summary>Complete source repair diff against the launch commit</summary>

```diff
diff --git a/phoenix/__main__.py b/phoenix/__main__.py
index df072ac..f8dbe71 100644
--- a/phoenix/__main__.py
+++ b/phoenix/__main__.py
@@ -51,20 +51,33 @@ def main():
         print("Evidence audit: " + ("FAILED" if errors else "passed"))
         return 1 if errors else 0
     if args.command == "replay":
-        import importlib.util
+        import tempfile
+        import types
         from pathlib import Path
-        if Path(args.run_id).name != args.run_id:
-            parser.error("run_id must be a published record id")
-        directory = ROOT / "docs/evidence" / args.run_id
-        if not (directory / "verifier.py").exists():
-            parser.error("This early record has no archived verifier; use verify --candidate instead")
-        spec = importlib.util.spec_from_file_location("archived_verifier", directory / "verifier.py")
-        module = importlib.util.module_from_spec(spec)
-        spec.loader.exec_module(module)
-        kwargs = {}
-        if (directory / "predecessor.py").exists():
-            kwargs["predecessor"] = directory / "predecessor.py"
-        report = module.evaluate(directory / "server.py", **kwargs)
+        from .audit import EvidenceError, verified_run
+        try:
+            archived, artifacts = verified_run(ROOT / "docs", args.run_id)
+            if archived["status"] == "generation-failed":
+                raise EvidenceError("Generation failed; there is no executable candidate to replay")
+            if "verifier.py" not in artifacts:
+                raise EvidenceError("This record has no archived verifier; use verify --candidate instead")
+        except EvidenceError as error:
+            print("Replay refused: " + str(error), file=sys.stderr)
+            return 2
+        # Execute precisely the verified bytes, without importing stale bytecode
+        # or reopening source files that may have changed after the audit.
+        module = types.ModuleType("archived_verifier")
+        exec(compile(artifacts["verifier.py"], "archived-verifier.py", "exec"), module.__dict__)
+        with tempfile.TemporaryDirectory(prefix="phoenix-replay-") as temporary:
+            directory = Path(temporary)
+            candidate = directory / "server.py"
+            candidate.write_bytes(artifacts["server.py"])
+            kwargs = {}
+            if archived.get("predecessor_sha256"):
+                predecessor = directory / "predecessor.py"
+                predecessor.write_bytes(artifacts["predecessor.py"])
+                kwargs["predecessor"] = predecessor
+            report = module.evaluate(candidate, **kwargs)
         print(json.dumps(report, indent=2))
         return 0 if report["status"] == "passed" else 1
 
diff --git a/phoenix/audit.py b/phoenix/audit.py
index 7576210..dd45237 100644
--- a/phoenix/audit.py
+++ b/phoenix/audit.py
@@ -1,49 +1,128 @@
-"""An integrity check, not a signature or third-party attestation."""
+"""Check and snapshot evidence before use; hashes are not an attestation."""
+import hashlib
 import json
 from pathlib import Path
-from .records import sha
+import re
+
+
+CHECK_IDS = {
+    "C01", "C02", "C03", "C04", "C05", "C06", "C07", "C08",
+    "S01", "S02", "S03", "S04", "S05", "R01", "R02", "R03", "R04", "D01", "D02",
+}
+
+
+class EvidenceError(ValueError):
+    """The requested record cannot be verified as a complete published bundle."""
+
+
+def valid_run_id(value):
+    return isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]*", value) is not None
+
+
+def read_ledger(site):
+    try:
+        ledger = json.loads((Path(site) / "runs.json").read_text(encoding="utf-8"))
+    except (OSError, ValueError) as error:
+        raise EvidenceError("Cannot read the public ledger: " + type(error).__name__) from error
+    if not isinstance(ledger, list):
+        raise EvidenceError("The public ledger must be a list")
+    seen = set()
+    for row in ledger:
+        if not isinstance(row, dict) or not valid_run_id(row.get("id")):
+            raise EvidenceError("Invalid or unsafe run id in the public ledger")
+        if row["id"] in seen:
+            raise EvidenceError("Duplicate run id in the public ledger: " + row["id"])
+        seen.add(row["id"])
+    return ledger
+
+
+def inspect_bundle(directory, summary):
+    """Return checked bytes, so replay need not reopen mutable source files."""
+    directory = Path(directory)
+    if directory.is_symlink() or not directory.is_dir():
+        raise EvidenceError("Missing run directory or unsupported directory symlink")
+
+    def read_artifact(name):
+        if not isinstance(name, str) or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", name) is None:
+            raise EvidenceError("Unsafe artifact name")
+        path = directory / name
+        if path.is_symlink() or not path.is_file():
+            raise EvidenceError("Missing artifact or unsupported symlink: " + name)
+        return path.read_bytes()
+
+    try:
+        manifest = json.loads(read_artifact("manifest.json"))
+        if not isinstance(manifest, dict):
+            raise EvidenceError("Manifest must be an object")
+        if not {"server.py", "contract.md", "report.json"} <= set(manifest):
+            raise EvidenceError("Missing required manifest entries")
+        artifacts = {}
+        for name, expected in manifest.items():
+            if not isinstance(expected, str) or re.fullmatch(r"[0-9a-f]{64}", expected) is None:
+                raise EvidenceError("Invalid artifact digest")
+            raw = read_artifact(name)
+            if hashlib.sha256(raw).hexdigest() != expected:
+                raise EvidenceError("Artifact digest mismatch: " + name)
+            artifacts[name] = raw
+        # Derived __pycache__ directories from older replay versions are ignored;
+        # no replay code is imported from them or written into the archive.
+        files = {p.name for p in directory.iterdir() if p.is_file() or p.is_symlink()} - {"manifest.json"}
+        if files != set(manifest):
+            raise EvidenceError("Unlisted or missing top-level artifacts")
+        report = json.loads(artifacts["report.json"])
+        if not isinstance(report, dict):
+            raise EvidenceError("Report must be an object")
+        for key, value in summary.items():
+            if report.get(key) != value:
+                raise EvidenceError("ledger differs from report: " + key)
+        bound = {"server.py": "candidate_sha256", "contract.md": "contract_sha256"}
+        if report.get("verifier_sha256"):
+            bound["verifier.py"] = "verifier_sha256"
+        if report.get("predecessor_sha256"):
+            bound["predecessor.py"] = "predecessor_sha256"
+        for filename, field in bound.items():
+            if filename not in artifacts or hashlib.sha256(artifacts[filename]).hexdigest() != report.get(field):
+                raise EvidenceError("Report digest mismatch or missing artifact: " + filename)
+        checks = report["checks"]
+        if not isinstance(checks, list) or any(not isinstance(c, dict) or type(c.get("passed")) is not bool for c in checks):
+            raise EvidenceError("Invalid check results")
+        if type(report["passed"]) is not int or type(report["total"]) is not int:
+            raise EvidenceError("Check counts must be integers")
+        if report["passed"] != sum(c["passed"] for c in checks) or report["total"] != len(checks):
+            raise EvidenceError("Incorrect check counts")
+        if report["status"] not in ("passed", "failed", "generation-failed"):
+            raise EvidenceError("Unknown verdict")
+        if report["status"] == "passed" and not (
+            len(checks) == len(CHECK_IDS) and all(c["passed"] for c in checks)
+            and {c.get("id") for c in checks} == CHECK_IDS
+        ):
+            raise EvidenceError("Invalid passing verdict or check identifiers")
+    except EvidenceError:
+        raise
+    except (OSError, KeyError, TypeError, ValueError) as error:
+        raise EvidenceError("Incomplete evidence bundle: " + type(error).__name__) from error
+    return report, artifacts
+
+
+def verified_run(site, run_id):
+    if not valid_run_id(run_id):
+        raise EvidenceError("run_id must be a published record id")
+    row = next((r for r in read_ledger(site) if r["id"] == run_id), None)
+    if row is None:
+        raise EvidenceError("Run is not present in the public ledger: " + run_id)
+    return inspect_bundle(Path(site) / "evidence" / run_id, row)
 
 
 def audit(site):
     site = Path(site)
+    try:
+        ledger = read_ledger(site)
+    except EvidenceError as error:
+        return [str(error)]
     errors = []
-    ledger = json.loads((site / "runs.json").read_text())
-    seen = set()
     for summary in ledger:
-        run_id = summary["id"]
-        if run_id in seen or Path(run_id).name != run_id:
-            errors.append("Duplicate or unsafe run id")
-            continue
-        seen.add(run_id)
-        directory = site / "evidence" / run_id
         try:
-            report = json.loads((directory / "report.json").read_text())
-            manifest = json.loads((directory / "manifest.json").read_text())
-            if not {"server.py", "contract.md", "report.json"} <= set(manifest):
-                errors.append(run_id + ": missing required manifest entries")
-            for name, digest in manifest.items():
-                if Path(name).name != name or sha(directory / name) != digest:
-                    errors.append(run_id + ": artifact digest mismatch: " + name)
-            for key, value in summary.items():
-                if report.get(key) != value:
-                    errors.append(run_id + ": ledger differs from report: " + key)
-            if sha(directory / "server.py") != report.get("candidate_sha256"):
-                errors.append(run_id + ": candidate digest mismatch")
-            if sha(directory / "contract.md") != report.get("contract_sha256"):
-                errors.append(run_id + ": contract digest mismatch")
-            if (directory / "verifier.py").exists() and sha(directory / "verifier.py") != report.get("verifier_sha256"):
-                errors.append(run_id + ": verifier digest mismatch")
-            if report.get("predecessor_sha256") and sha(directory / "predecessor.py") != report["predecessor_sha256"]:
-                errors.append(run_id + ": predecessor digest mismatch")
-            checks = report["checks"]
-            if report["passed"] != sum(c["passed"] for c in checks) or report["total"] != len(checks):
-                errors.append(run_id + ": incorrect check counts")
-            if report["status"] == "passed" and not (len(checks) == 19 and all(c["passed"] for c in checks)):
-                errors.append(run_id + ": invalid passing verdict")
-            if report["status"] == "passed" and {c["id"] for c in checks} != {
-                "C01", "C02", "C03", "C04", "C05", "C06", "C07", "C08",
-                "S01", "S02", "S03", "S04", "S05", "R01", "R02", "R03", "R04", "D01", "D02"}:
-                errors.append(run_id + ": passing verdict has incorrect check identifiers")
-        except (OSError, KeyError, TypeError, ValueError) as error:
-            errors.append(run_id + ": incomplete evidence bundle (" + type(error).__name__ + ")")
+            inspect_bundle(site / "evidence" / summary["id"], summary)
+        except EvidenceError as error:
+            errors.append(summary["id"] + ": " + str(error))
     return errors
```

</details>

## Verification

All commands below exited 0 on 2026-10-21. These are local checks; GitHub CI
independently checks Python 3.9, 3.12 and 3.13 after the commit is pushed.

```text
python3 -m compileall -q phoenix examples/reference
(no output; exit 0)

python3 -m unittest discover -s tests -v
test_an_always_200_service_cannot_claim_success (test_experiment.AcceptanceHarnessTests) ... ok
test_recreating_database_at_boot_fails_data_continuity (test_experiment.AcceptanceHarnessTests) ... ok
test_reference_meets_contract_with_a_real_predecessor_database (test_experiment.AcceptanceHarnessTests) ... ok
test_ledger_cannot_change_a_verdict_without_matching_report (test_experiment.PublishedEvidenceTests) ... ok
test_modified_candidate_is_detected (test_experiment.PublishedEvidenceTests) ... ok
test_published_ledger_is_internally_consistent (test_experiment.PublishedEvidenceTests) ... ok
test_extra_unlisted_file_is_refused (test_replay_integrity.ReplayIntegrityTests) ... ok
test_generation_failure_is_not_treated_as_executable_source (test_replay_integrity.ReplayIntegrityTests) ... ok
test_malformed_ledger_produces_a_controlled_error (test_replay_integrity.ReplayIntegrityTests) ... ok
test_manifest_cannot_omit_a_verifier_that_replay_would_load (test_replay_integrity.ReplayIntegrityTests) ... ok
test_modified_candidate_is_refused_before_startup (test_replay_integrity.ReplayIntegrityTests) ... ok
test_modified_verifier_is_refused_before_execution (test_replay_integrity.ReplayIntegrityTests) ... ok
test_replay_uses_verified_snapshot_if_disk_files_change_after_inspection (test_replay_integrity.ReplayIntegrityTests) ... ok
test_symlinked_artifact_is_refused_even_when_bytes_match (test_replay_integrity.ReplayIntegrityTests) ... ok
test_unsafe_and_unlisted_run_ids_are_refused (test_replay_integrity.ReplayIntegrityTests) ... ok
test_valid_archive_replays_without_writing_into_evidence (test_replay_integrity.ReplayIntegrityTests) ... ok

----------------------------------------------------------------------
Ran 16 tests in 3.085s

OK

python3 -m phoenix audit
Evidence audit: passed
```

The ten new [replay regression tests](../../tests/test_replay_integrity.py) cover
modified verifier/candidate source, omitted verifier manifest entries, extra
artifacts, symlinks, unsafe and unlisted IDs, malformed ledgers, generation
failures, unchanged valid replay, and source replacement after validation.
The existing six harness tests remain in place, including false-green and
old-data-destruction negative cases.

<details><summary>Full independent reference HTTP acceptance output (19/19)</summary>

Command: `python3 -m phoenix verify`. This is a reference verification,
not another AI reconstruction.

```json
{
  "schema_version": 1,
  "kind": "reference-verification",
  "started_at": "2026-10-21T02:46:33.347729+00:00",
  "candidate_sha256": "857cb24936ce6e0a7a941466b57b01fb8125748a1eb197306a62d664616cf142",
  "checks": [
    {
      "id": "C01",
      "title": "Service boots from its documented interface",
      "category": "contract",
      "passed": true,
      "duration_ms": 339.58
    },
    {
      "id": "C02",
      "title": "Health exposes the schema version",
      "category": "contract",
      "passed": true,
      "duration_ms": 0.46
    },
    {
      "id": "S01",
      "title": "Administrative routes reject missing and wrong credentials",
      "category": "security",
      "passed": true,
      "duration_ms": 2.85
    },
    {
      "id": "C03",
      "title": "Create a named inbox with an unpredictable token",
      "category": "contract",
      "passed": true,
      "duration_ms": 1.18
    },
    {
      "id": "C04",
      "title": "Capture a real UTF-8 webhook request",
      "category": "contract",
      "passed": true,
      "duration_ms": 1.15
    },
    {
      "id": "C05",
      "title": "Preserve body, metadata, and safe headers",
      "category": "contract",
      "passed": true,
      "duration_ms": 0.74
    },
    {
      "id": "R01",
      "title": "Deduplicate deliveries and reject body conflicts",
      "category": "reliability",
      "passed": true,
      "duration_ms": 1.24
    },
    {
      "id": "R02",
      "title": "Concurrent retries create one logical event",
      "category": "reliability",
      "passed": true,
      "duration_ms": 15.78
    },
    {
      "id": "R03",
      "title": "Unkeyed deliveries remain independent",
      "category": "reliability",
      "passed": true,
      "duration_ms": 4.76
    },
    {
      "id": "R04",
      "title": "Idempotency keys are scoped to an inbox",
      "category": "reliability",
      "passed": true,
      "duration_ms": 2.58
    },
    {
      "id": "C06",
      "title": "Bound pagination and list newest events first",
      "category": "contract",
      "passed": true,
      "duration_ms": 3.73
    },
    {
      "id": "S02",
      "title": "Reject malformed inbox input",
      "category": "security",
      "passed": true,
      "duration_ms": 3.18
    },
    {
      "id": "S03",
      "title": "Reject oversized and malformed request bodies",
      "category": "security",
      "passed": true,
      "duration_ms": 2.25
    },
    {
      "id": "C07",
      "title": "Accept non-JSON and empty webhook bodies",
      "category": "contract",
      "passed": true,
      "duration_ms": 3.03
    },
    {
      "id": "S04",
      "title": "Keep credentials out of storage responses and logs",
      "category": "security",
      "passed": true,
      "duration_ms": 2.23
    },
    {
      "id": "S05",
      "title": "Reject incorrect hook tokens without revealing inboxes",
      "category": "security",
      "passed": true,
      "duration_ms": 1.03
    },
    {
      "id": "C08",
      "title": "Unknown resources and unsupported methods return JSON",
      "category": "contract",
      "passed": true,
      "duration_ms": 1.63
    },
    {
      "id": "D01",
      "title": "Events and idempotency survive process restart",
      "category": "compatibility",
      "passed": true,
      "duration_ms": 169.41
    },
    {
      "id": "D02",
      "title": "Read old events and preserve their idempotency state",
      "category": "compatibility",
      "passed": true,
      "duration_ms": 1.09
    }
  ],
  "execution": "local-subprocess",
  "evaluation_isolation": "procedural",
  "human_interventions": null,
  "predecessor_sha256": "857cb24936ce6e0a7a941466b57b01fb8125748a1eb197306a62d664616cf142",
  "compatibility_fixture": "database populated via predecessor HTTP API before candidate startup",
  "duration_seconds": 0.56,
  "passed": 19,
  "total": 19,
  "status": "passed"
}
```

</details>

<details><summary>Full archived candidate replay output (19/19)</summary>

Command: `python3 -m phoenix replay 20261021T024534Z-55970b`.
This repeats acceptance on the new candidate without another model call.

```json
{
  "schema_version": 1,
  "kind": "reference-verification",
  "started_at": "2026-10-21T02:46:34.018977+00:00",
  "candidate_sha256": "202394078f6f72cfb81f05218177da4c57255247ef7d24bb97e183c36b670c0f",
  "checks": [
    {
      "id": "C01",
      "title": "Service boots from its documented interface",
      "category": "contract",
      "passed": true,
      "duration_ms": 542.76
    },
    {
      "id": "C02",
      "title": "Health exposes the schema version",
      "category": "contract",
      "passed": true,
      "duration_ms": 0.73
    },
    {
      "id": "S01",
      "title": "Administrative routes reject missing and wrong credentials",
      "category": "security",
      "passed": true,
      "duration_ms": 4.18
    },
    {
      "id": "C03",
      "title": "Create a named inbox with an unpredictable token",
      "category": "contract",
      "passed": true,
      "duration_ms": 1.77
    },
    {
      "id": "C04",
      "title": "Capture a real UTF-8 webhook request",
      "category": "contract",
      "passed": true,
      "duration_ms": 1.81
    },
    {
      "id": "C05",
      "title": "Preserve body, metadata, and safe headers",
      "category": "contract",
      "passed": true,
      "duration_ms": 1.12
    },
    {
      "id": "R01",
      "title": "Deduplicate deliveries and reject body conflicts",
      "category": "reliability",
      "passed": true,
      "duration_ms": 1.59
    },
    {
      "id": "R02",
      "title": "Concurrent retries create one logical event",
      "category": "reliability",
      "passed": true,
      "duration_ms": 14.91
    },
    {
      "id": "R03",
      "title": "Unkeyed deliveries remain independent",
      "category": "reliability",
      "passed": true,
      "duration_ms": 2.79
    },
    {
      "id": "R04",
      "title": "Idempotency keys are scoped to an inbox",
      "category": "reliability",
      "passed": true,
      "duration_ms": 2.34
    },
    {
      "id": "C06",
      "title": "Bound pagination and list newest events first",
      "category": "contract",
      "passed": true,
      "duration_ms": 3.95
    },
    {
      "id": "S02",
      "title": "Reject malformed inbox input",
      "category": "security",
      "passed": true,
      "duration_ms": 3.63
    },
    {
      "id": "S03",
      "title": "Reject oversized and malformed request bodies",
      "category": "security",
      "passed": true,
      "duration_ms": 1.83
    },
    {
      "id": "C07",
      "title": "Accept non-JSON and empty webhook bodies",
      "category": "contract",
      "passed": true,
      "duration_ms": 3.25
    },
    {
      "id": "S04",
      "title": "Keep credentials out of storage responses and logs",
      "category": "security",
      "passed": true,
      "duration_ms": 2.18
    },
    {
      "id": "S05",
      "title": "Reject incorrect hook tokens without revealing inboxes",
      "category": "security",
      "passed": true,
      "duration_ms": 1.02
    },
    {
      "id": "C08",
      "title": "Unknown resources and unsupported methods return JSON",
      "category": "contract",
      "passed": true,
      "duration_ms": 1.59
    },
    {
      "id": "D01",
      "title": "Events and idempotency survive process restart",
      "category": "compatibility",
      "passed": true,
      "duration_ms": 164.33
    },
    {
      "id": "D02",
      "title": "Read old events and preserve their idempotency state",
      "category": "compatibility",
      "passed": true,
      "duration_ms": 1.04
    }
  ],
  "execution": "local-subprocess",
  "evaluation_isolation": "procedural",
  "human_interventions": null,
  "predecessor_sha256": "9a4c995e30016c43248aab0721ee1c068991831ffdf528b851848c167f074f35",
  "compatibility_fixture": "database populated via predecessor HTTP API before candidate startup",
  "duration_seconds": 0.759,
  "passed": 19,
  "total": 19,
  "status": "passed"
}
```

</details>

## Today's bounded reconstruction

One generation cycle was run with a maximum of two attempts and 300 seconds
per attempt. Attempt 1 passed 19/19 checks, so there was no second attempt.
The [published report](../evidence/20261021T024534Z-55970b/report.json),
[source](../evidence/20261021T024534Z-55970b/server.py) and
[manifest](../evidence/20261021T024534Z-55970b/manifest.json)
are preserved in the notebook ledger.

- Record: `20261021T024534Z-55970b` (timestamps in the report are UTC).
- Predecessor: `20261020T091931Z-90cdc2`, the latest passing AI candidate before this run.
- Predecessor SHA-256: `9a4c995e30016c43248aab0721ee1c068991831ffdf528b851848c167f074f35`.
- Candidate SHA-256: `202394078f6f72cfb81f05218177da4c57255247ef7d24bb97e183c36b670c0f`.
- Continuity fixture: an inbox and event created through the predecessor's HTTP
  API before starting the new candidate against the same SQLite database.
- Generation: authenticated `codex-cli 0.154.0`, clean default configuration,
  no explicit model override, 49.249 seconds, zero observed tool calls.
- Resolved model identity and monetary cost remain unknown. Usage is copied only
  from the CLI's actual response; inspect the report for those counts.

No new credentials, model settings or account permissions were created or
changed. No third-party messages or promotion were sent. The previously
published failed attempt remains visible.
