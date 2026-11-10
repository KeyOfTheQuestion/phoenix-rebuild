# 2026-11-10 read-only delivery history pagination

Roadmap item 8's local attempt journal can now be browsed beyond the newest
100 records. `delivery-history --cursor TOKEN` continues from a returned
`next_cursor`; `delivery_history_page` provides the same page API to Python
callers. The legacy Python list API and SQLite schema remain. The
[client specification](../delivery.md#continue-delivery-history) defines cursor
encoding, tie-breaking, refusal behavior and cross-page consistency limits.

Each read-only query orders by start time and ID, returns at most 1..100 rows
and uses one extra row to detect continuation. Equal timestamps use the ID
tie-breaker. Newer keys cannot shift continuation, while final receipts and
late/backdated writes can become visible. This is an unsigned seek position,
not a transaction snapshot or a permission token. History does not resend,
relabel uncertainty or change database contents.

## Actual client observations

The expanded smoke uses today's real AI inbox,
[`20261110T025401Z-6dcf54`](../evidence/20261110T025401Z-6dcf54/report.json).
Both [CPython 3.9.6 / SQLite 3.43.2](2026-11-10-delivery-sources/python39.json)
and [CPython 3.14.6 / SQLite 3.53.3](2026-11-10-delivery-sources/python314.json)
passed **38/38**. D01 now also pages a real AI-backed delivery's saved receipt,
checking its event ID, policy hash and terminal cursor. Existing D/J/K checks
remain, with eight history checks added:

| Check | Observed behavior |
| --- | --- |
| H01 | Traverse all 123 synthetic rows, exactly once and newest first, without changing journal bytes. |
| H02 | Equal timestamps use the ID tie-breaker across page boundaries. |
| H03 | A newer inserted row appears on a new first page without shifting continuation. |
| H04 | Receipt finalization is visible on later pages without changing order or interpreting uncertainty as failure. |
| H05 | Malformed, oversized and noncanonical cursors refuse before an instrumented database boundary, without echoing input. |
| H06 | Empty and exact terminal pages return a null cursor; page limits remain 1..100. |
| H07 | CLI continuation returns older rows and redacts invalid-cursor diagnostics without modifying the journal. |
| H08 | The legacy list API remains; unsigned seek positions stay parameterized even with SQL-like key text. |

Pagination fixtures use synthetic local SQLite rows and timestamps, not claims
of actual delivery on those fixture dates. The
[snapshot manifest](2026-11-10-delivery-sources/manifest.json) binds the AI inbox,
worker, journal, policy, HTTP helper, four check modules, runner, CLI, record
support and both reports. These are matching-checkout observations, not a
standalone replay package. D12 remains worker-protocol fault injection; H05
instruments the database boundary. Other checks use actual HTTP, files, SQLite
or processes. The smoke makes no model call and asserts that sources and the
public AI ledger stay unchanged. Existing CI/Pages run the expanded smoke.

## Review and negative controls

The first pagination test failed on the missing page API. After implementation,
H01..H08 passed. Fresh-context review found no production defect, but identified
two test gaps: a missing journal alone did not prove validation preceded file
access, and canonical encoding guards lacked otherwise valid noncanonical inputs.

H05 now instruments the database boundary, requires a cursor-specific refusal,
and covers JSON whitespace, unnecessary Unicode escapes and nonzero base64 pad
bits. Separate in-memory omissions of the JSON and base64 canonical checks
produced **two** and **one** failing subcases respectively. The unchanged guards
passed. No repository source was changed by those negative controls, and none
is counted as an AI attempt or a new backend acceptance check.

## Complete-suite failure retained

The [unit observation metadata](2026-11-10-unit-observations.json) preserves all
three complete-suite observations:

| Runtime/run | Outcome | Duration |
| --- | --- | --- |
| CPython 3.9.6, initial concurrent run | 149 tests, one failure | 80.346 s |
| CPython 3.9.6, serial repeat | 149/149 passed | 79.879 s |
| CPython 3.14.6 | 149/149 passed | 39.626 s |

The initial failure was the existing frozen
`20261102T020351Z-0ba772` runtime-matrix test: replay returned exit 1 rather than
0, with empty stderr. Its assertion omitted stdout, so the specific failed
backend check was not retained in that original test output. **The cause remains
unconfirmed.** A separate [diagnostic replay](2026-11-10-legacy-matrix-diagnostic.json)
of the same verified bytes passed **19/19**. No historical candidate or verifier
was edited, no pass rule was relaxed, and no automatic retry was added.

The assertion now includes stdout when it fails, preserving future acceptance
details. The serial repeat then passed the entire floor suite. The initial
failure is not relabelled as a pass or assigned a guessed failing check. Raw
development logs remain private because tracebacks can contain local paths;
published metadata binds their retained SHA-256 values. Existing newer-Python
SQLite resource warnings also remain disclosed.

Independent current reference HTTP acceptance passed **19/19** and the evidence
audit passed. This host has no Docker or optional Chromium installation; the
existing remote container/browser workflows provide those publication checks.
The [plan and rulings](../superpowers/plans/2026-11-10-history-pagination.md)
record this diagnostic change alongside the bounded pagination work.

## Today's sole bounded reconstruction cycle

Existing authenticated **codex-cli 0.166.0**, clean defaults and installed
**CPython 3.9.6 / SQLite 3.43.2** produced the only real cycle. The cap was two
attempts of at most 300 seconds each. **Attempt 1 passed 32/32**, so no second
model call was made. Generated source was not manually edited.

| Actual observation | Value |
| --- | --- |
| AI record | `20261110T025401Z-6dcf54` |
| Acceptance began (UTC) | 2026-11-10 02:53:59.733050 |
| Generation duration | 92.522 seconds |
| Observed input / output / reasoning tokens | 22999 / 3835 / 127 |
| Cached / cache-write input tokens | 0 / 0 |
| Observed tool calls | 0 |
| Resolved model identity / monetary cost | Unknown / unknown |

The 32-check migration fixture still uses the latest eligible v1 AI predecessor,
`20261103T023452Z-4ec8a9`. The separate
[v2 continuity bundle](2026-11-10-continuity/report.json) uses yesterday's latest
successful v2 AI, `20261109T023041Z-6e6fad`, and passed **8/8**, with disclosed
synthetic timestamp aging. The [frozen AI runtime matrix](2026-11-10-ai-matrix.json)
passed **32/32** on both installed runtimes. These are separate from the older
archive unit-test failure above and made no model calls. The public AI ledger
gained exactly one attempt; its previous 24 rows and historical artifacts remain
intact.

## Limits

Page size bounds returned rows, not sorting work or total scan latency. Cursors
are unsigned and not bound to a journal identity; use a returned cursor with
the same journal for normal continuation. Cross-page reads are not a frozen
snapshot. This adds no schema migration, index, external host, durable queue,
recovery resend or change to backend reconstruction contracts. Existing trusted
local file, SQLite rollback-mode and local-process assumptions remain.
