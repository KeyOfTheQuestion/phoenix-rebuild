# V2 continuity implementation plan

> **For agentic workers:** Use superpowers:executing-plans inline, with one independent read-only review before publication.

**Goal:** Complete roadmap item 10 with a distinct, inspectable v2-to-v2 policy/data continuity fixture.

**Architecture:** Add `phoenix/continuity.py` and `verify-continuity` / `replay-continuity` commands. Capture the candidate, predecessor, contract, verifier and HTTP helper once; execute those copies and optionally publish a separate six-artifact bundle plus manifest. No AI ledger row or change to the existing 19/32-check rules.

**Spec:** Roadmap item 10 and contract 002's idempotent v2 startup, persistent policies, explicit expiry and idempotency semantics.

**Tech stack:** Python 3.9+ standard library, existing HTTP Subject helper and local subprocess execution.

## Fixture and check IDs

- V01: A real v2 predecessor creates three inboxes, enabled/explicitly-disabled/longer policies and four keyed UTF-8 events via HTTP. Disclosed synthetic timestamp aging happens while stopped; restart the predecessor and capture its logical rows and HTTP details before handoff.
- V02: Candidate starts as schema 2 without modifying any inbox, event or policy row.
- V03: Existing enabled/disabled/longer policy responses survive unchanged.
- V04: Existing bodies, headers, timestamps, hook tokens, duplicate IDs and conflict behavior survive.
- V05: Candidate restart retains all logical rows, policies and idempotency behavior; no implicit expiry.
- V06: Explicit expiry removes only the oldest active event within limit 1.
- V07: Disabled and longer-policy events remain protected.
- V08: Expired key can create a replacement; its duplicate state and policies survive restart.

## Tasks and verification gates

1. [ ] Add tests for reference success, policy-wiping and idempotency-breaking candidates, implicit deletion, a v1 predecessor refusal, frozen snapshots, complete failure bundles, manifest/binding tamper refusal, safe replay and CLI exit codes. Observe expected missing-feature failures.
2. [ ] Implement the standalone frozen verifier, complete non-ledger bundle and pre-execution replay validation. Leave existing contracts/checkers/reference programs unchanged.
3. [ ] Integrate explicit commands and CI/Pages smoke checks for reference and published continuity bundles. Assert that no public AI ledger is modified.
4. [ ] Run full units, independent HTTP acceptance and audit. Make one bounded real clean-default CLI cycle (max two attempts, 300 seconds each) using the v1 predecessor required by the unchanged 32-check fixture. Then check its resulting v2 candidate against the latest successful v2 AI predecessor with the new fixture.
5. [ ] Publish actual outcomes and frozen source, including any failures, unknown model/costs and explicit fixture aging. Keep English/Chinese README and notebook consistent. Obtain independent review, commit/push and verify CI, Pages and live bytes.

## Constraints and rulings

- The daily authorization covers inline implementation and main publication without a new approval gate.
- This is a separate eight-check observation, never a relabelled 32/32 result, extra AI attempt or model benchmark.
- Local subprocess execution retains normal user permissions. Docker generation/replay of existing profiles is unchanged; this new fixture is local only.
- Frozen maintenance bundles are historical evidence and must not be overwritten. Replay uses verified captured bytes and writes only temporary copies.
- Ruling during required regression checks: both reference servers inherit a five-connection listen queue and fail a controlled eight-client admission test. Set their queue to 32, preserve the original failed acceptance report, and keep the shared acceptance checks and all archived programs intact.
