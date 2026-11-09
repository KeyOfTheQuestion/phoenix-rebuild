# 2026-11-09 captured loopback destination policy

Roadmap item 8 now has an optional exact receiver allowlist.
`deliver-event --destination-policy FILE` validates a bounded regular UTF-8 JSON
file and every listed URL, then requires the selected URL to match exactly.
Invalid policies and unmatched destinations refuse before either HTTP operation
or journal creation. The [policy specification](../delivery.md#optional-destination-policy)
defines the schema, exact-match behavior, capture semantics and limits.

Every returned final receipt binds the captured bytes with
`destination_policy_sha256`. Persisted final journal receipts retain it as well;
no policy URL, path or contents are copied there. Interrupted unconfirmed rows
keep their existing format and do not establish which policy was used. This
is an optional operator guard for one invocation, not account-wide enforcement,
an external-host permission or a durable queue.

## Actual client observations

The expanded smoke uses today's real AI inbox,
[`20261109T023041Z-6e6fad`](../evidence/20261109T023041Z-6e6fad/report.json).
Both [CPython 3.9.6 / SQLite 3.43.2](2026-11-09-delivery-sources/python39.json)
and [CPython 3.14.6 / SQLite 3.53.3](2026-11-09-delivery-sources/python314.json)
passed **30/30**. D01 now also sends an actual inbox event through the policy
guard, checking its exact body and captured policy hash. The existing D01..D12
and J01..J09 checks remain, with nine focused policy checks added:

| Check | Observed behavior |
| --- | --- |
| K01 | An exactly approved URL receives original bytes; stdout and the final journal bind the captured policy hash without storing private contents. |
| K02 | Changed ports, paths, queries and escaping refuse before HTTP or journal creation. |
| K03 | Validate the complete policy, including unused entries; refuse malformed UTF-8/JSON, duplicate keys, wrong types/versions, unknown fields and duplicate URLs. |
| K04 | Accept exactly 16,384 bytes and 16 entries; refuse the next byte or entry. |
| K05 | An edit during source GET does not change the captured decision/hash; the next invocation uses the changed file and refuses. |
| K06 | Rejected, source-error and delivery-unknown outcomes retain the same policy binding in stdout and final journal receipts. |
| K07 | CLI policy approval/refusal returns the expected code and omits token, destination and file-path values from diagnostics/receipts. |
| K08 | Missing files, directories and FIFOs refuse before any HTTP or journal activity. |
| K09 | Repeated directory/FIFO refusals release their actual POSIX file descriptors. |

The [source snapshot manifest](2026-11-09-delivery-sources/manifest.json) binds
the AI inbox, delivery worker, policy, journal, HTTP helper, three check modules,
runner, CLI, record support and both reports. These are observations of the
matching checkout, not a standalone archive replay command. Payloads, policies
and receiver services are synthetic. D12 remains explicit worker-protocol fault
injection; other checks use actual HTTP, files, SQLite or processes. The smoke
makes no model call and asserts unchanged measured sources and public AI ledger.
Existing CI and Pages run the expanded smoke through their prior workflows.

## Development and review

The first focused test failed because the client did not yet accept a `policy`
argument. After implementation, the 20 delivery/policy tests passed on Python
3.9. A fresh-context code review found no actionable defect and independently
ran the original eight policy checks on Python 3.14.6.

A subsequent resource-lifetime check found that `os.fdopen` could fail for a
directory before owning/closing the descriptor. K09 reproduced **28 descriptors
instead of the initial 8** after repeated refusals. The loader now checks the
raw descriptor, performs a bounded read and closes it in a `finally` block.
All nine policy checks then passed, followed by both complete suites and
actual-AI delivery smokes. Private development logs retain the initial failures;
they are not represented as model attempts or backend acceptance checks.

## Verification

The complete dependency-free unit suite passed **141 tests** on CPython 3.9.6
in **80.170 seconds**, and **141 tests** on CPython 3.14.6 in **37.029 seconds**.
Independent reference HTTP acceptance passed **19/19** and the evidence audit
passed. Existing newer-Python SQLite resource warnings remain disclosed.
This host has no Docker or optional Chromium installation; the established
remote container and browser workflows provide those checks before publication.

The [implementation plan](../superpowers/plans/2026-11-09-destination-policy.md)
records the bounded scope and review rulings. Backend contracts, generation
configuration, shared acceptance code and reference implementations retain their
prior bytes. Only the journal's final-receipt whitelist is extended; its SQLite
schema and old rows are unchanged.

## Today's one bounded reconstruction cycle

Existing authenticated **codex-cli 0.166.0**, clean defaults and installed
**CPython 3.9.6 / SQLite 3.43.2** produced the sole real cycle. The cap was two
attempts of at most 300 seconds each. **Attempt 1 passed 32/32**, so no second
model call was made. Generated source was not manually edited.

| Actual observation | Value |
| --- | --- |
| AI record | `20261109T023041Z-6e6fad` |
| Acceptance began (UTC) | 2026-11-09 02:30:39.985862 |
| Generation duration | 91.045 seconds |
| Observed input / output / reasoning tokens | 22999 / 3588 / 111 |
| Cached / cache-write input tokens | 0 / 0 |
| Observed tool calls | 0 |
| Resolved model identity / monetary cost | Unknown / unknown |

The 32-check migration fixture still uses the latest eligible v1 AI predecessor,
`20261103T023452Z-4ec8a9`. The separate
[v2 continuity bundle](2026-11-09-continuity/report.json) uses yesterday's latest
successful v2 AI, `20261108T023025Z-c3520d`, and passed **8/8**, with disclosed
synthetic timestamp aging. The [frozen AI runtime matrix](2026-11-09-ai-matrix.json)
passed **32/32** on both installed runtimes. Those checks made no model calls.
The public AI ledger gained exactly one attempt; the previous 23 rows and
historical artifact bytes remain intact.

## Limits

The receiver list accepts only the existing literal IPv4 loopback HTTP URLs.
Exact string matching deliberately does not normalize equivalent URLs. Policy
edits after capture affect the next invocation; they do not cancel a running
POST. Omitting the flag retains the original explicit-URL client behavior.

Policies are plaintext trusted local configuration, and their hashes are
comparison values rather than encryption or independent attestation. Captured
bytes are bounded, but arbitrary filesystem I/O is outside the worker's network
deadline. FIFO/descriptor controls are POSIX observations. Unconfirmed journal
rows make no policy-usage claim. History never reauthorizes or resends a row.
Delivery to external hosts, destination rules for those hosts, durable queues
and recovery resends remain future work.
