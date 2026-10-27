# 2026-10-27: retention and v1 migration reference pilot

Contract 002 now has a complete written specification, an AI-assisted reference
implementation and a separate focused HTTP acceptance pilot. The new reference
extends the existing development baseline; it is not an independently generated
candidate and is not counted as an AI success.

```sh
python3 -m phoenix verify-retention
python3 -m phoenix verify-retention \
  --predecessor docs/evidence/20261027T023955Z-76bf63/server.py
```

- [Complete contract 002](../../specs/webhook-inbox-retention.md).
- [Standalone reference](../../examples/retention/server.py).
- [Focused verifier](../../phoenix/retention.py).
- [Structured local verification](2026-10-27-retention-pilot.json).

## What changed

Startup adds a `retention_policies` table and commits `PRAGMA user_version=2`
in one transaction. Existing inbox/event columns and logical rows are preserved,
including IDs, token hashes, bodies, headers, timestamps and idempotency keys.
Valid v1 databases at user-version 0 or 1 are supported; version 2 startup is
idempotent. Unknown versions are refused before schema changes. Named writes
support equivalent v1 column orders without rebuilding tables. The reference
avoids `executescript` during migration because it would implicitly commit.

Retention defaults to disabled. An authenticated administrator can GET/PUT
`/api/inboxes/{id}/retention` with `max_age_seconds` null or integer 1..2592000.
Only an explicit POST to `/api/inboxes/{id}/expire` removes events. Its optional
`limit` is 1..100, default 100. Each transaction reads the policy and deletes at
most that many oldest eligible events from that inbox. A deleted event releases
its idempotency key; its replacement becomes an ordinary deduplicated event.
Reads, policy updates, webhook reception and startup do not delete implicitly.

The service serializes expiry with other writers, preserves recent/other-inbox
and disabled-policy events, and commits before reporting the actual deletion
count. The row limit is not a scan-time or full resource bound. There is no
background scheduler, production deployment or deleted-event recovery. A
supplied trusted program runs locally with the user's process permissions.
The pilot is not connected to the Docker runner or reconstruction adapter yet.

## What the 13 checks establish

The verifier starts a real v1 predecessor and creates an inbox/event via HTTP.
It snapshots all logical v1 rows, stops the predecessor, starts the reference
against the same database, compares those rows and checks the original hook
credential, UTF-8 body, headers and idempotent duplicate behavior. Restart and
default-disabled checks happen before any fixture aging.

Only afterward, it creates additional synthetic events via the v2 HTTP API,
stops the process and backdates those events directly in SQLite. It does not
backdate the original v1 migration event. This makes expiry checks deterministic
without claiming that a real retention interval elapsed. Fixture timestamps use
both UTC `Z` and `+00:00` forms. The report discloses this method explicitly.

The focused checks cover:

- M01–M04: real v1 HTTP data, row/credential/idempotency preservation, disabled
  defaults and repeated startup;
- S01/P01/P02: authorization before lookup, methods, malformed policies/limits,
  bounds and configuration persistence;
- F01/E01/E02: disclosed aging without implicit deletion, oldest-first limits
  and concurrent batches without double-counting;
- E03/E04/P03: recent/other-inbox/longer-policy isolation, expired-key reuse and
  disabling a policy across restart.

Both the v1 development reference and today's archived v1 AI candidate were used
as predecessors in actual local runs. Both passed all 13 checks. The structured
record binds candidate, predecessor, contract, verifier and shared HTTP client
hashes and retains actual timestamps and durations. These are bounded synthetic
fixtures, not an exhaustive migration proof or independent attestation.

## Verification and negative controls

| Local check | Observed result |
| --- | --- |
| Dependency-free unit suite | 48 passed, 8.311 seconds |
| Independent contract-001 reference HTTP acceptance | 19/19 passed |
| Retention pilot with v1 reference predecessor | 13/13 passed |
| Retention pilot with today's v1 AI predecessor | 13/13 passed |
| Public evidence audit | Passed |
| Python compilation and notebook JavaScript syntax | Passed |
| Real notebook browser suite | 8 passed, 12.279 seconds |

Nine new unit controls exercise the reference with the latest successful AI
predecessor and preserve the public ledger. Negative candidates that always
return 200, destructively rebuild old tables, ignore expiry or also delete recent
events are rejected. A future-version database is refused without changing its
schema/data/version. An injected error after policy-table creation rolls back
the new table and version without changing v1 rows. A separate deliberately
reordered SQL fixture verifies
named writes and preserved column order. CLI pass/fail controls confirm that the
pilot never calls the AI evidence publisher. These controls call no model.

The browser run used Chromium 156.0.8078.4 and Playwright 1.64.0 and checked every
published record and artifact. The new pilot is required by the existing CI
Python 3.9/3.12/3.13 checks and Pages publication workflow; no new provider
credentials or workflow permissions were added.

## Today's single real reconstruction remains contract 001

The one actual model cycle used the existing authenticated Codex CLI, its clean
default configuration, at most two attempts and a 300-second limit per attempt.
Attempt 1 passed 19/19, so no second attempt was made. The latest successful AI
candidate before the run, October 26's record, populated the continuity database
through HTTP. The October 27 candidate then read that same database and retained
its event/idempotency state.

- Record: [`20261027T023955Z-76bf63`](../evidence/20261027T023955Z-76bf63/report.json).
- Acceptance started: `2026-10-27T02:39:55.121774+00:00` (UTC).
- Candidate SHA-256: `9f148de136d44219ebce207764364b2ccba7d0f0291c271f0d3a72adbce851af`.
- Predecessor record: `20261026T020534Z-d08ffd`.
- Predecessor SHA-256: `f6be34011759fc083dac477e8b635e93af56abc8359cb3b7205dbfa8db27a9cb`.
- CLI: `codex-cli 0.163.0`; clean default; resolved model unknown.
- Generation: 55.738 seconds; zero observed tool calls; no manual candidate edit.
- Actual returned usage: 22,988 input, 3,072 output and 175 reasoning output
  tokens; zero cached/cache-write input tokens. Monetary cost remains unknown.
- Execution: local subprocess, because this host has no Docker runtime.

The contract-001 specification, 19-check verifier and historical evidence were
unchanged. Existing failures remain in the ledger. No model override, new
credentials, permission change or credit purchase was used.

## Remaining scope

The public reconstruction ledger, replay and browser metrics still describe
contract 001. The 13-check reference pilot does not claim full contract-002
acceptance or AI reconstruction. A later iteration must extend full v2
acceptance and bind the selected contract/harness/archive format before allowing
v2 AI records. Nothing in this change relabels a v1 run or counts a reference
check as a model success.
