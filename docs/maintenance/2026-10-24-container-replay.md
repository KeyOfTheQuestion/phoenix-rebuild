# 2026-10-24: replay the original archived verifier inside Docker

The container-runner roadmap follow-up now includes archived replay:

```sh
python3 -m phoenix replay RUN_ID --container --timeout 90
```

Replay first checks the ledger, manifest, report bindings and artifact hashes.
It then sends the already verified candidate, archived verifier and recorded
predecessor bytes to the restricted Docker runner. The host does not compile,
import or execute the archived verifier in this mode. It does not reopen the
original archive files after validation. A missing Docker runtime is refused
without host fallback.

`container-verify` still uses the current harness; `replay --container` uses the
selected historical harness. Records without a recorded predecessor retain the
archived verifier's original SQL-fixture behavior. Result JSON identifies the
record with `replay_run_id` and `replay_source`, plus contract, verifier,
candidate and optional predecessor hashes. No original outcome or archive is
rewritten, and replay makes no model call.

The image, resource policy and boundary probe are unchanged from the
[container profile](../container-runner.md). The shared container/UID and shared
host kernel remain limits for hostile-code and adversarial-evaluator claims.
Daily generation remains local; replay without `--container` retains local
execution.

## Focused regression controls

Seven new unit cases cover:

- selecting a deliberately different verifier supplied as immutable bytes;
- rejecting mutable/string snapshot inputs before starting Docker;
- forwarding the exact verified archive bytes and record metadata;
- refusing modified evidence before contacting the runtime;
- retaining an early SQL-fixture verifier without inventing a predecessor;
- retaining checked bytes when files change after validation;
- refusing unavailable Docker without executing a valid archived verifier on
  the host.

The last control uses a disposable archive copy with an internally consistent
manifest and a verifier that would write a marker if loaded on the host. The
marker stays absent in container mode, including when Docker is unavailable.
The unit Docker client is mocked; those tests are not described as actual
container execution. Published historical files are not modified by the controls.

## Local verification

| Check | Result |
| --- | --- |
| `python3 -m unittest discover -s tests -v` | 31 tests passed, 2.789 seconds |
| `python3 -m phoenix verify` | Reference HTTP acceptance: 19/19 passed |
| Local replay of today's AI archive | 19/19 passed |
| Local replay of early AI archive `20261020T091318Z-b37ad8` | 19/19 passed |
| `python3 -m phoenix audit` | Passed |
| Maintained Python compilation and notebook JavaScript syntax | Passed |
| Notebook Chromium suite | 7/7 passed |

The local host has no Docker executable. The actual command
`python3 -m phoenix replay 20261024T023201Z-51147a --container`
exited 2 with:

```text
Replay refused: Docker is not installed; container verification did not run
```

Browser output from the real local Chromium run:

```text
Browser: Chromium 156.0.8078.4; Playwright 1.64.0
test_contract_navigation_loads_the_published_specification (__main__.NotebookBrowserTests.test_contract_navigation_loads_the_published_specification) ... ok
test_every_evidence_dialog_links_to_the_matching_archived_bytes (__main__.NotebookBrowserTests.test_every_evidence_dialog_links_to_the_matching_archived_bytes) ... ok
test_filters_switch_between_all_records_and_ai_only (__main__.NotebookBrowserTests.test_filters_switch_between_all_records_and_ai_only) ... ok
test_language_switch_persists_after_reload (__main__.NotebookBrowserTests.test_language_switch_persists_after_reload) ... ok
test_metrics_match_the_published_ledger (__main__.NotebookBrowserTests.test_metrics_match_the_published_ledger) ... ok
test_unavailable_ledger_shows_an_actionable_error (__main__.NotebookBrowserTests.test_unavailable_ledger_shows_an_actionable_error) ... ok
test_unavailable_report_allows_a_retry (__main__.NotebookBrowserTests.test_unavailable_report_allows_a_retry) ... ok

----------------------------------------------------------------------
Ran 7 tests in 8.752s

OK
```

## Real Linux CI verification

The existing `container` CI job and Pages publication check execute
`python3 tests/container_smoke.py`. It runs a current-reference check, then uses
the actual `replay --container` CLI for:

1. the latest audited AI record, including its archived verifier and predecessor;
2. early AI record `20261020T091318Z-b37ad8`, whose archived verifier is different
   from today's current verifier and whose original fixture uses SQL.

The script asserts all selected source/contract hashes against the verified
record, checks 19/19 acceptance for each subject, and retains the existing
boundary observations and deliberate timeout/cleanup control. Its completed log
emits a `CONTAINER_EVIDENCE=` JSON record. Actual runtime results come from that
Linux job, separate from the local mock-based controls and local reconstruction.

- Snapshot API implementation: [phoenix/container.py](../../phoenix/container.py).
- Container entrypoint SHA-256: `9745924e4b8c3b55b26a14dd6f1d6b275d50ba4324192a5ec49a77f4519937f7`.
- Early archived verifier SHA-256: `b1a9886eb8da776647db536e731e87899a77c3e3b4633b73c944f8917ac48d9c`.
- Current verifier SHA-256: `435142a64f7d62b54d58c5187973e237ca2140d25833b464d3d68279c0fdab6d`.

## Today's single bounded reconstruction

One cycle allowed at most two attempts and 300 seconds per attempt. Attempt 1
passed 19/19 HTTP checks, so no second attempt was made. The latest successful
AI candidate available before this run supplied the real predecessor database.

- Record: [`20261024T023201Z-51147a`](../evidence/20261024T023201Z-51147a/report.json).
- Acceptance started at `2026-10-24T02:32:00.456782+00:00` (UTC).
- Candidate SHA-256: `4b9710cda28f5272290dfd5d14967f15518684bcffb8af2a998cde4c38359476`.
- Predecessor record: `20261023T023013Z-e63562`.
- Predecessor SHA-256: `35c8091e910d57db44858879aedd4d5316f43909f4b96140c799131688cbbaba`.
- Generation: 58.384 seconds, zero observed tool calls, clean CLI default.
- This reconstruction's execution mode remains `local-subprocess`.

The installed CLI reported `codex-cli 0.163.0`; the previous daily record
reported `0.154.0`. This run used the existing installation and did not issue
an upgrade command or model override. The resolved model identity is still
unknown, so the version change does not support any claim about which model
ran. Usage is only the actual CLI response in the report, and monetary cost
remains unknown.

The contract and independent HTTP verifier were unchanged. Historical failed
attempts stay in the ledger. No credentials or account permissions were created
to work around a failure.
