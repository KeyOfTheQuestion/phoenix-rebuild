# 2026-11-06 v2 policy and data handoff

Roadmap item 10 now has a distinct eight-check fixture. A v2 predecessor creates
enabled, explicitly disabled and longer-policy inboxes plus four keyed UTF-8
events through HTTP. The candidate opens that same database and must preserve
the policies, every logical row, HTTP details, hook tokens and idempotency
behavior. Further checks exercise restart, bounded expiry, protected events and
replacement of an expired key.

## Actual AI-to-AI observation

The receiving program was today's real AI candidate
[`20261106T025938Z-ba9ca5`](../evidence/20261106T025938Z-ba9ca5/report.json).
The predecessor was the latest successful v2 AI program,
[`20261105T023912Z-690f7b`](../evidence/20261105T023912Z-690f7b/report.json).

```sh
python3 -m phoenix verify-continuity \
  --candidate docs/evidence/20261106T025938Z-ba9ca5/server.py \
  --predecessor docs/evidence/20261105T023912Z-690f7b/server.py \
  --output-dir path/to/new-continuity-bundle
python3 -m phoenix replay-continuity docs/maintenance/2026-11-06-continuity
```

The [complete frozen handoff bundle](2026-11-06-continuity/manifest.json)
passed **8/8** on CPython **3.9.6 / SQLite 3.43.2**, beginning at
03:04:54.921806 UTC. A [fresh replay](2026-11-06-continuity-python314.json)
also passed **8/8** on CPython **3.14.6 / SQLite 3.53.3**.

| Frozen input | SHA-256 |
| --- | --- |
| Receiving AI program | `c071cb0cd024ebf01cd5fc263ed77ad941d0e6c264a3157f1926c3f31df8840f` |
| Previous v2 AI program | `dfa7d4030b30f2a33bacbc172459ddc265aeb25d70f92b2aeb5bab0a996a6be9` |
| Continuity verifier | `04ea41766fa1b1339697ea7fab1a721905b1b91b1f1de98da342c43424c36c5c` |
| Shared HTTP helper | `494d66e9c3e66b33596ebb0ba55e68d3db32347bc1e658c3bc1f0a87f3ebe0b1` |
| Contract 002 | `8585891ec40f537a09ee306442845532c19d22122182b21f0f6041b2288bb374` |

Only synthetic event timestamps are backdated in SQLite while the predecessor
is stopped. The predecessor is restarted and inspected again before handoff.
This checks an intentionally aged fixture rather than a real elapsed retention
period. Reports identify the method and measured execution runtime.

## Reproducible negative control

A [separate failed bundle](2026-11-06-continuity-negative/report.json) contains
a deliberately modified reference that deletes retention policies at startup.
Its predecessor is the same November 5 AI program. Fixture creation passed V01;
V02 detected changed predecessor rows and stopped. The result is **failed,
1 of 2 executed checks passed**, with eight required for a complete pass.
Its complete source, failed report and manifest are retained. This synthetic
control makes no model call and adds no AI attempt.

## Capture, replay and failure handling

The command captures five inputs before any subject runs. Optional fresh output
directories contain the candidate, predecessor, contract, continuity verifier,
HTTP helper, report and manifest. Replay reads and validates the exact file set,
all manifest digests, five report bindings, profile, runtime observation and
sequential verdict counts before loading archived code. It executes verified
bytes from temporary copies. Existing output destinations are refused.

Checks V01–V08 stop at their first unmet prerequisite. Their failure reports and
source snapshots can be archived just like complete passes. Public diagnostics
use verifier-authored messages; candidate-controlled errors, temporary paths
and generated credentials are kept out of reports.

Review caught an integration mistake that required every historical replay to
pass. A regression reproduced that failure before the fix. CI/Pages now retain
fresh validated outcomes for both passed and failed handoff bundles. Reference
controls still require 8/8, and all source bindings and counts must validate.
Thus published failures remain inspectable alongside fresh runtime observations.

## Verification

Eleven initial unit controls passed on Python 3.9. They exercise real HTTP
behavioral faults, destructive initialization, a v1 predecessor, path changes
after capture, immutable replay, complete failed bundles, missing/empty/incorrect
bindings, rehashed manifests, symlinks, output collisions and CLI exit codes.
A further regression covers failed observations in the publication smoke.
The final full-suite results are recorded below after that integration repair.

Python 3.9.6's initial **110-test** suite passed. Several full Python 3.14 runs
then exposed intermittent reference acceptance failures. Added diagnostics
captured [B-R02 failing with a connection reset](2026-11-06-reference-connection-failure.json)
while all other 31 checks passed. That report refers to the original retention
reference source at commit `245e688`, whose SHA-256 is
`10ecaa50408ecc618224bca8ee26af4840869bc1c3ae1d24ca91cd4bffecbf95`.
All local failure logs are retained.

The reference programs inherited the standard HTTP server's five-connection
listen queue. A controlled regression delayed acceptance for one second and
opened the verifier's eight-client burst. Both old entrypoints timed out.
Giving each reference server an explicit queue of 32 let the same regression
pass. This fixes admission capacity while preserving the actual concurrent
idempotency test. Both reference sources change today; original archived
reference bytes and failed observations remain intact. Assertions now include
failed check details or the returned replay report for future diagnostics.

After the repair, the complete **111-test** suite passed on Python 3.9.6 in
**66.487 seconds** and Python 3.14.6 in **25.961 seconds**. Independent reference
acceptance passed 19/19; the [composed reference observation](2026-11-06-reference-fixed.json)
passed 32/32. The updated retention reference hash is
`b3a3805541703822233900c2b6b942f515a215c8a9f4e9bdbf17c29247535622`.
Existing SQLite resource warnings in newer Python tests remain disclosed.

Independent reference HTTP acceptance passed **19/19**. The evidence audit,
Python compilation and notebook JavaScript syntax check passed. Existing
CI/Pages run `tests/continuity_smoke.py`, exercising a temporary reference bundle
and independently replaying the published positive and negative handoffs.
The marker is `CONTINUITY_EVIDENCE=`, with archived and fresh outcomes.

This host has no Docker or optional Chromium test installation. The existing
remote workflows run their container and browser checks. The new continuity
commands use local subprocess execution with normal user permissions.

## One bounded real reconstruction

Today's only real generation cycle used the already authenticated
**codex-cli 0.166.0**, clean default configuration and system CPython **3.9.6**.
It allowed at most two attempts with 300 seconds each. **Attempt 1 passed 32/32**,
so no second model call was made. The receiving source was not manually edited.

| Actual generation observation | Value |
| --- | --- |
| Acceptance began (UTC) | 2026-11-06 02:59:36.454502 |
| Generation duration | 92.359 seconds |
| Input / output / reasoning tokens | 22872 / 3733 / 111 |
| Cached / cache-write input tokens | 0 / 0 |
| Observed tool calls | 0 |
| Resolved model identity / monetary cost | Unknown / unknown |

The full 32-check fixture still uses the latest successful v1 AI predecessor,
`20261103T023452Z-4ec8a9`, to exercise migration. The subsequent eight-check
handoff uses the newer v2 predecessor above. These are separate observations;
the public AI ledger gains exactly one real attempt. The
[frozen AI runtime matrix](2026-11-06-ai-matrix.json) passed 32/32 on both installed
Python versions, independently of the eight continuity checks.

Contract text, shared baseline/retention code, existing generator profiles
and historical evidence keep their prior bytes today.
The new fixture samples three policies and four predecessor events. The observed
pair and runtimes define its practical scope.
