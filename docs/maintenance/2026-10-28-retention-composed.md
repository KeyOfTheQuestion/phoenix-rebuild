# 2026-10-28: composed retention acceptance

`verify-retention --full` now combines the existing 13 migration/retention checks
with the 19 baseline backend HTTP checks, explicitly expecting schema version 2.
The default retention command keeps its focused 13 checks. Both commands are
reference verification, not AI reconstruction, and do not publish ledger rows.

```sh
python3 -m phoenix verify-retention
python3 -m phoenix verify-retention --full
```

The [structured evidence](2026-10-28-retention-composed.json) preserves actual
positive and negative-control results, timestamps, source hashes and phase
counts. The [retention reference](../../examples/retention/server.py) and both
written contracts were unchanged by this iteration.

## Why compose the checks

The focused pilot tests migration, policy configuration and expiry behavior.
Those checks alone do not recheck every original backend requirement. The
composed mode also tests original-body preservation, request-size/encoding
limits, administrative authentication, credential privacy, concurrent delivery
idempotency, pagination, restart and predecessor data continuity.

Baseline check IDs receive `B-` prefixes so they cannot collide with focused
IDs such as `S01`. A combined pass requires both phase verdicts to pass and all
32 distinct check IDs to be present. The report includes `expected_total: 32`,
actual executed counts and each phase's count/duration/verdict. An incomplete
phase cannot become a combined pass by merely labelling itself successful.

Candidate and predecessor bytes are read once before either phase, staged as
local temporary copies and used for both. Each phase creates a separate
synthetic database and populates it through the predecessor's real HTTP API.
This is one pair of source snapshots, not one database shared between the two
phases. Their returned source hashes must match the captured bytes. Focused
contract/verifier/shared-client hashes must match values captured at the start,
otherwise there is no combined verdict. These consistency checks retain normal
local process permissions and do not protect against adversarial programs.

The focused phase still explicitly backdates only synthetic v2 events while
stopped, after its migration checks. This does not claim that a real-world
retention interval elapsed. The two-phase suite is bounded acceptance, not
proof of every possible database state or absence of implementation bugs.

## Deliberate body-limit regression

A disposable reference copy was changed from `if size > 65536:` to
`if size > 65537:`. This deliberately accepts one body byte more than either
contract allows. No model generated this control, and the actual reference
source was not modified.

| Subject/mode | Actual outcome |
| --- | --- |
| Correct reference, focused mode | 13/13 passed |
| Correct reference, composed mode | 32/32 passed |
| Mutated reference, focused phase inside composition | 13/13 passed |
| Mutated reference, composed mode | 31/32 passed; overall failed |

The only failed check was `B-S03`, the baseline malformed/oversized-body check.
This demonstrates a real coverage difference: passing the focused retention
checks does not imply that original backend boundaries still hold. The public
JSON includes the exact mutation rule, original source hash, mutated candidate
hash and full actual failed report. It is not an AI failure or success entry.

## Explicit verifier method change

The shared baseline `evaluate` accepts a keyword-only
`expected_schema_version`, validated to integer 1 or 2 before starting a subject.
The default is still 1. Only the expected health value is parameterized; the
original 19 IDs, check operations and passing rule remain the default behavior.
New baseline reports identify the selected expectation explicitly.

This changes the shared verifier source hash. Historical verifier files and
maintenance reports are unchanged. Archived replay continues to execute each
record's own verifier. The ledger audit now refuses a baseline report with an
explicit schema expectation other than 1, even if its 19 checks pass. Historical
records without the newer field remain valid. A schema-v2 check cannot silently
become a contract-001 published result through that new selector.

Today's model cycle started before this edit. It froze and archived the original
verifier (`435142a64f7d62b54d58c5187973e237ca2140d25833b464d3d68279c0fdab6d`)
before generation; its recorded result is not reassigned to the changed shared
verifier or the 32-check suite. The composed evidence identifies the newer
shared verifier source separately.

## Verification

Nine new controls check the 32-check pass, the body-limit negative case, original
file replacement between phases, missing checks, inconsistent verification
bindings, the unchanged 19 default v1 IDs, invalid schema expectations, CLI mode
selection and refusal to label a schema-v2 baseline as contract 001. All fixtures
are temporary; the public AI ledger remains unchanged by these reference tests.

The existing CI and Pages workflows require both retention modes. V1 reference
acceptance, unit controls, the browser suite, the existing real container
regressions and the evidence audit remain publication checks. V2 generation,
container composition and archive integration are still follow-ups; the public
reconstruction ledger and notebook metrics continue to describe contract 001.

| Local check | Observed result |
| --- | --- |
| Dependency-free unit suite | 57 passed, 22.150 seconds |
| Contract-001 reference HTTP acceptance | 19/19 passed |
| Default focused retention mode | 13/13 passed |
| Composed retention mode | 32/32 passed |
| Deliberately incorrect body-limit control | 31/32; correctly failed at B-S03 |
| Evidence audit | Passed |
| Python compilation and notebook JavaScript syntax | Passed |
| Real notebook browser suite | 8 passed, 18.407 seconds |

The browser run used Chromium 156.0.8078.4 and Playwright 1.64.0. It checked every
published record/artifact and retained the existing failure-recovery controls.

## Today's single bounded contract-001 reconstruction

One actual cycle allowed at most two attempts and 300 seconds per attempt using
the existing authenticated CLI and its clean default configuration. Attempt 1
passed 19/19; no second attempt was made. The previous successful AI candidate
populated the same SQLite continuity database through HTTP before replacement.

- Record: [`20261028T023142Z-5f1624`](../evidence/20261028T023142Z-5f1624/report.json).
- Acceptance started: `2026-10-28T02:31:41.769609+00:00` (UTC).
- Candidate SHA-256: `7c258e2fcaa928a0f1452b8cb590440bf75bcfdc7ce12d084f03024b186cc822`.
- Predecessor: `20261027T023955Z-76bf63`, SHA-256
  `9f148de136d44219ebce207764364b2ccba7d0f0291c271f0d3a72adbce851af`.
- CLI: `codex-cli 0.163.0`; resolved model identity unknown.
- Generation: 53.619 seconds, zero observed tool calls, no manual candidate edit.
- Actual returned usage: 22,990 input, 2,938 output and 129 reasoning output
  tokens; zero cached/cache-write input tokens. Monetary cost remains unknown.
- Execution: local subprocess; Docker is unavailable on this host.

Historical failures remain public. No model override, account permission change,
new credentials or credit purchase was used. The contract-001 specification and
its archived evidence were not edited.
