# 2026-11-07 loopback delivery pilot

Roadmap item 8 now has a bounded client pilot. `deliver-event` reads a stored
event and posts its original UTF-8 body to an explicitly selected IPv4 loopback
HTTP receiver. The [client specification](../delivery.md) defines address policy,
limits, phase markers and receipt statuses.

The worker validates the source body digest and selected headers, uses a fresh
receiver connection, and attempts at most one POST. A parent-enforced deadline
covers startup and both HTTP operations, including slow streams. After a POST
has begun, an unavailable response produces an uncertain outcome. A retained
phase marker also survives partial worker output. Three protocol regression
subcases first failed before the parent validated HTTP status against receipt
claims and retained that marker independently of a truncated final result.

## Actual delivery observations

The smoke used today's real AI inbox,
[`20261107T023127Z-4a21fe`](../evidence/20261107T023127Z-4a21fe/report.json),
with independent synthetic HTTP receivers. Both
[Python 3.9.6 / SQLite 3.43.2](2026-11-07-delivery-sources/python39.json) and
[Python 3.14.6 / SQLite 3.53.3](2026-11-07-delivery-sources/python314.json)
passed **12/12**.

| Check | Observed behavior |
| --- | --- |
| D01 | An actual AI inbox event arrives as the exact UTF-8 bytes, query and selected headers. |
| D02 | Source and receiver redirects are reported directly. |
| D03 | Receiver 503 produces one rejected attempt. |
| D04 | Invalid IDs/digests/bodies/headers, oversized source JSON and wrong source credentials prevent POST. |
| D05 | Invalid addresses, credentials and deadlines refuse before network use. |
| D06 | Source credentials and unselected captured headers stay out of the POST. |
| D07 | The receiver observes a POST, but a deadline before its response leaves `delivery-unknown`. |
| D08 | A source sending small chunks cannot extend the overall deadline. |
| D09 | Receiver response inspection is capped, with its contents omitted from receipts. |
| D10 | The CLI reads the token environment and returns the specified exit codes. |
| D11 | Empty and 65,536-byte UTF-8 bodies arrive exactly. |
| D12 | Contradictory and truncated worker messages retain truthful outcomes. |

D12 uses protocol fault injection. The other controls use actual loopback HTTP;
their payloads, credentials and receiving services are synthetic. The
[source snapshot manifest](2026-11-07-delivery-sources/manifest.json) binds the
inbox, worker, HTTP helper, checks, runner, CLI, record support and both reports.
These are checkout observations. Reproduce with the recorded commit and its
matching files. The snapshots support source inspection rather than a standalone
archive-replay command. Existing historical evidence retains its original bytes.

## Verification

The complete dependency-free unit suite passed **123 tests** on CPython 3.9.6
in **62.666 seconds**, and **123 tests** on CPython 3.14.6 in **27.416 seconds**.
Independent reference HTTP acceptance passed **19/19** and the evidence audit
passed. Existing SQLite resource warnings remain disclosed for newer Python.
CI and Pages now run `tests/delivery_smoke.py` against the latest successful
v2 AI program and emit `DELIVERY_EVIDENCE=`. Those controls make no model call
and assert that the public AI ledger is unchanged.

This host has no Docker or optional Chromium installation. Existing remote
container and browser checks continue through the established workflows.

## Today's bounded reconstruction and handoff

The only real generation cycle used existing authenticated **codex-cli 0.166.0**,
clean defaults and the installed system **CPython 3.9.6 / SQLite 3.43.2**.
It permitted two attempts at most, 300 seconds each. **Attempt 1 passed 32/32**,
so no second model call was made. The generated candidate was not manually edited.

| Actual observation | Value |
| --- | --- |
| AI record | `20261107T023127Z-4a21fe` |
| Acceptance began (UTC) | 2026-11-07 02:31:25.253419 |
| Generation duration | 82.263 seconds |
| Observed input / output / reasoning tokens | 22997 / 3751 / 102 |
| Cached / cache-write input tokens | 0 / 0 |
| Observed tool calls | 0 |
| Resolved model identity / monetary cost | Unknown / unknown |

The 32-check migration fixture used the latest eligible v1 AI predecessor,
`20261103T023452Z-4ec8a9`. The separate
[v2 handoff bundle](2026-11-07-continuity/report.json) used the latest v2 AI
predecessor, `20261106T025938Z-ba9ca5`, and passed **8/8**. It preserves policies,
events and idempotency through the checked handoff/restart states, with disclosed
synthetic timestamp aging. The
[frozen AI matrix](2026-11-07-ai-matrix.json) separately passed **32/32** on both
installed Python runtimes. The public AI ledger gained one real attempt.

## Remaining delivery work

The current client accepts explicit IPv4 loopback HTTP endpoints. Delivery to
other hosts, a destination policy, durable attempt storage and outbox semantics
remain follow-ups. A second invocation can send the same event again, and the
receiver owns its idempotency behavior. A `delivered` receipt records a 2xx
response; downstream application processing is outside this observation.

Backend contracts, shared acceptance checks, reference implementations and
generation configuration retain their prior bytes today. No credits, new
provider credentials, model override or account-permission change was involved.
