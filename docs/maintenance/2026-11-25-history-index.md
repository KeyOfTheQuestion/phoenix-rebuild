# Ordered delivery history for new journals · 2026-11-25

New delivery journals create an index on `(started_at DESC, id DESC)` in the
same transaction that creates the table. The completed file, including the
index, is published through the existing atomic-link initialization. The
column layout, journal version, receipts, cursor format and read-only query
path retain their behavior. Existing files without the index are not upgraded
automatically and remain readable and writable.

The H23 regression first failed against the original implementation: the
actual first-page query planned `SCAN delivery_attempts` and
`USE TEMP B-TREE FOR ORDER BY`. After adding the index, all 24 history tests
passed. H23 captures the actual SQL issued by the page API and checks the
first-page and cursor-page plans; it also checks the returned record order.
H24 removes the optional index to model the prior logical format, traverses
it without changing its bytes, then appends and finalizes an uncertain receipt
without silently installing the index.

The separate plan observation uses 123 synthetic rows. It compares the
current indexed journal with a fixture whose order index was explicitly
removed. Both return IDs 123, 122, 121 and 120 across two pages and retain
identical file bytes during reads. On CPython 3.9.6 / SQLite 3.43.2, the
indexed first page scans the order index and continuation searches it using
the `(started_at, id)` seek. Both unindexed queries require a temporary
order-by tree. This is a controlled current-code comparison, not execution
of an archived older implementation. It measures plans, not elapsed time.
Other filters can still scan many rows; extra index storage and write work
are incurred, and no query-latency bound is claimed.

Today's single bounded contract-002 reconstruction used the existing
authenticated Codex CLI 0.166.0 with its clean default and passed 32/32 on
the first attempt. It ran on CPython 3.9.6 / SQLite 3.43.2, using the
successful v1 AI candidate `20261103T023452Z-4ec8a9` for the migration fixture.
The CLI reported 93.229 generation seconds, 24,842 input tokens, 4,040 output
tokens and 204 reasoning output tokens. The resolved model and monetary cost
are unknown. [Reconstruction report](../evidence/20261125T023206Z-b399d4/report.json).

Before committing, all 169 unit tests passed on Python 3.9, the independent
reference HTTP acceptance passed 19/19, and the evidence audit passed.
The separate v2 handoff from the November 24 candidate to today's candidate
passed 8/8, preserving HTTP-created data and policies before the disclosed
synthetic expiry fixture. Its bundle remains outside the AI ledger and the
32-check verdict. [Continuity report](2026-11-25-continuity/report.json).

The full client smoke, invoked from outside the checkout, passed all 58 checks
using today's actual AI inbox. H23 and H24 are client verification items,
separate from the backend's 32 checks. The frozen sources bind both the smoke
and plan observation by SHA-256.
[58-check report](2026-11-25-delivery-sources/python39.json),
[query plans and fixture](2026-11-25-delivery-sources/query-plans.json),
[source manifest](2026-11-25-delivery-sources/manifest.json).
