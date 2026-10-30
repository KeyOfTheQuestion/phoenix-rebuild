# 2026-10-30: container replay of frozen contract-002 archives

The existing Docker runner now replays the complete `retention-composed-v1`
reference archive. This closes the container-replay follow-up for contract 002;
AI generation remains contract 001. No historical bundle is modified or
republished, and replay makes no model call.

```sh
python3 -m phoenix replay 20261029T023917Z-bce8be --container --timeout 90
```

## Inputs, execution and output validation

Replay first audits the ledger, manifest, complete artifact set and report
bindings. It passes the checked candidate, predecessor, standalone verifier,
contract, baseline, retention and two support modules as immutable bytes to
Docker. Only these eight files plus the maintained container entrypoint are
mounted read-only; the old report is not mounted as an acceptance result.
Changes to archive paths after validation cannot replace the captured inputs.

The archived entrypoint receives its real `/input/verifier.py` filename so it
can find its archived siblings and create its private package on temporary
storage. None of that archived code is compiled or loaded on the host. The
current checkout's retention implementation and verifier are not substituted.

The host requires the reference-only contract-002 profile, version 2.0.0,
integer schema expectation 2 and expected coverage 32. All eight source/contract
hashes must match their staged bytes. A passing result must contain exactly the
32 distinct composed check identifiers. The existing actual boundary probe,
non-root user, disabled external network, read-only filesystem and resource
limits remain required. Runtime errors, timeouts, invalid reports and missing
Docker return exit code 2 without host fallback.

The image and policy are unchanged; see the [container profile](../container-runner.md).
The candidate and evaluator share the container and Linux kernel. This remains
a cooperative reconstruction check, not an adversarial-code sandbox or an
independently attested credential. Contract-002 migration still uses real
predecessor HTTP data and explicitly backdates only later synthetic expiry
fixtures; no real retention interval is claimed to have elapsed.

## Verification and its limits

| Local observation | Result |
| --- | --- |
| Final dependency-free unit suite, Python 3.14.6 | 71 passed in 9.715 seconds |
| Final dependency-free unit suite, Python 3.9.6 | 71 passed in 28.577 seconds |
| [Independent contract-001 reference HTTP acceptance](2026-10-30-reference-v1.json) | 19/19 passed |
| [Existing frozen contract-002 local replay](2026-10-30-retention-local-replay.json) | 32/32 passed |
| Evidence audit | Passed |
| Python compilation | Passed |
| Explicit contract-002 Docker request on this host | Exit 2: Docker unavailable; no host execution |
| Additional Python 3.9.6 replay of today's AI candidate | **17/19 passed; overall failed**; C06 and C08 failed; preserved below |

Five new unit controls use a synthetic Docker client and the actual published
reference bundle. They check complete staging, absence of host verifier loading,
archive immutability, changes after validation, all eight incorrect digest
bindings, invalid contract/profile/kind/schema/count metadata, incomplete
passing coverage, tampered archives before Docker contact, and unsafe, missing
or mutable dependency inputs. They exercise staging and result validation;
they do not establish that a real Docker engine ran. Existing timeout/cleanup
and policy controls remain in the shared runner suite. The earlier host-marker
test now tests missing-runtime refusal, rather than unsupported v2 replay.

This migrated host uses Python 3.14.6 and has neither Docker nor the optional
Playwright package installed. No local Docker or Chromium success is claimed.
The existing CI Python 3.9/3.12/3.13 matrix checks supported runtime versions.
Both Linux CI and the Pages publication workflow run `tests/container_smoke.py`:
it now replays the real v2 reference alongside the existing v1/legacy fixtures,
requires 32/32, checks all eight hashes and unchanged archive bytes/directory
entries, and prints the actual report under `CONTAINER_EVIDENCE=` as
`retention_reference`. The existing Chromium suite still runs in both workflows.
The [actual Linux container job](https://github.com/KeyOfTheQuestion/phoenix-rebuild/actions/runs/40994339565/job/123863850052)
passed, including contract-002 replay at 32/32, all eight bindings and unchanged
archive contents. Its [captured result](2026-10-30-retention-container-result.json)
includes the actual probe, pinned image and hashes. The separate Chromium job
also passed all 9 checks. The overall first CI run failed in two Python 3.9
integrity tests; the cause and correction are disclosed next. Successful jobs
are not represented as a successful overall workflow.

## Cross-runtime failure discovered by CI

The first [CI run](https://github.com/KeyOfTheQuestion/phoenix-rebuild/actions/runs/40994339565)
failed `test_valid_archive_replays_without_writing_into_evidence` and
`test_replay_uses_verified_snapshot_if_disk_files_change_after_inspection` on
Python 3.9. Those integrity controls selected the newest AI record and assumed
its original passing verdict must still hold on every Python runtime.

Today's generated candidate calls `parse_qs(query, keep_blank_values=True,
strict_parsing=True)` even for an empty query. Python 3.9 rejects that empty
query with `ValueError`; Python 3.14 returns an empty mapping. On Python 3.9,
the candidate consequently returns HTTP 400 for default event-list requests,
including the missing-inbox case. This violates contract 001's Python 3.9+
requirement. The same frozen candidate/verifier/predecessor reproduced **17/19**
locally on Python 3.9.6, failing C06 and C08. The
[actual failed replay](2026-10-30-python39-ai-replay.json) is retained separately;
the original 19/19 observation on Python 3.14 and every candidate byte remain
unchanged. No extra model attempt or manual candidate repair was made.

Integrity tests now require the exact checked candidate/predecessor hashes,
successful startup, all 19 check IDs/counts, an internally correct fresh verdict
and the matching exit code, plus unchanged archive contents where applicable.
They allow a truthful acceptance-failure verdict when runtime behavior differs;
they do not force it to pass or skip the failing checks. All 15 integrity controls
passed locally on Python 3.9.6 after this correction. This correction distinguishes
archive integrity from runtime acceptance and does not fix the archived candidate's
Python 3.9 defect. Runtime identity and explicit candidate coverage at the supported
Python floor are a follow-up in the roadmap.

## Today's single real reconstruction

The real model cycle used the latest successful AI candidate from October 29
as predecessor. Attempt 1 passed 19/19 on Python 3.14.6; no second attempt was made. Generation
remained bounded to two attempts and 300 seconds each, using the existing
authenticated Codex CLI with clean default configuration and no model override.

- Record: [`20261030T024939Z-60a62e`](../evidence/20261030T024939Z-60a62e/report.json).
- Acceptance started at `2026-10-30T02:49:35.448178+00:00` (UTC).
- Candidate SHA-256: `ef206e5d9870e14533307b799330a97c12278953919b7e22470704e4c11e6ad6`.
- Predecessor: `20261029T023202Z-360ada`, SHA-256
  `626254c065586ec92e991959156d7b187cfa7038b103360f6a577c9a66105afc`.
- Verifier SHA-256: `ae420dcbc20b8599253a9af43dd31c6162a2acc2ca240a046e03148186482f8e`;
  the contract-001 verifier and acceptance rules are unchanged today.
- CLI observed on this host: `codex-cli 0.145.0`; yesterday's record used
  `codex-cli 0.163.0`. This environment change is disclosed, not hidden by
  rewriting previous records. Resolved model identity remains unknown.
- Generation: 61.716 seconds; zero observed tool calls; no manual candidate edit.
- Observed usage: 17,849 input, 4,803 output and 930 reasoning output tokens;
  zero cached/cache-write input tokens. Monetary cost remains unknown.
- Execution: local subprocess; predecessor-created HTTP data continuity passed.

The earlier scheduled check stopped before generation because local GitHub
push credentials were unavailable after the host migration. On the user's
explicit request, the official GitHub CLI was installed and its already-granted
OAuth access restored through the standard device flow. Existing scopes were
retained; no account permissions or model settings were expanded. No credential
value or private diagnostic log is included in this repository.
