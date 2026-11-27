# Preview journal indexing without writing · 2026-11-27

`delivery-journal-index --journal FILE --dry-run` opens an existing supported
journal read-only and inspects the order index inside one read transaction.
Its result always reports `created: false`; `would_create` distinguishes an
absent index from an existing canonical one. The preview does not build an
index or reserve the writer lock. A subsequent normal invocation rechecks
the journal under its own write transaction. The preview is a snapshot, not
a reservation or promise that a later upgrade will succeed.
[Command documentation](../delivery.md#preview-index-maintenance-without-writing).

The first real-CLI regression failed because `--dry-run` was unrecognized.
H31 checks the actual SQL trace for a read transaction with no DDL/DML and
verifies unchanged file bytes and directory entries. H32 exercises both CLI
plans without claiming creation. H33 covers table/view/index name collisions
and trigger compatibility; H34 previews while another connection holds a
reserved writer lock. H35 checks mode types before database access and
missing-journal refusal. H27 now repeats its full invalid-definition matrix
in preview and normal modes. These tests add no backend reconstruction items.

Review and an independent SQLite probe found a compatibility regression in
the initial schema lookup: triggers can share the index name, but an
unrestricted `sqlite_master` lookup treated that trigger as a collision.
The failing trigger regression is retained locally. The final lookup checks
the table/index/view namespace and preserves same-name triggers before and
after actual indexing. The independent reviewer reran all 35 history tests
and found no remaining blocker.

Today's single bounded contract-002 reconstruction used the existing
authenticated Codex CLI 0.166.0 with its clean default. Its first attempt
passed 32/32 on CPython 3.9.6 / SQLite 3.43.2. The required v1 migration fixture
used successful AI candidate `20261103T023452Z-4ec8a9`. The CLI reported
101.684 generation seconds, 24,251 input tokens, 4,163 output tokens and 212
reasoning output tokens. The resolved model and monetary cost remain unknown.
[Reconstruction report](../evidence/20261127T023413Z-b1bdf7/report.json).

All 180 unit tests passed on Python 3.9; independent reference HTTP acceptance
passed 19/19 and the evidence audit passed. The separate v2 handoff from the
November 26 candidate to today's candidate passed 8/8 using HTTP-created
data/policies and disclosed synthetic timestamp aging. Its frozen bundle
remains outside the AI ledger and 32-check verdict.
[Continuity report](2026-11-27-continuity/report.json).

The full client smoke invoked from outside the checkout passed all 69 checks
using today's actual AI inbox. Its frozen source snapshots bind the CLI,
journal implementation and final regression fixtures. The notebook ledger
contains the new reconstruction report; client maintenance observations remain
separate from that ledger.
[69-check report](2026-11-27-delivery-sources/python39.json),
[source manifest](2026-11-27-delivery-sources/manifest.json).
