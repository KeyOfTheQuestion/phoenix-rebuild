# Trace delivery attempts by inbox source · 2026-11-23

An event ID can appear in more than one inbox service. The new
`delivery-history --source ORIGIN` option narrows those attempts to the
SHA-256 of the exact source string passed during delivery. It combines with
the existing event, receiver, state and cursor predicates before the page
limit. Python callers can pass `source=ORIGIN` to `delivery_history_page`.
[Command documentation](../delivery.md#filter-delivery-history-by-source-origin).

The query uses the existing `source_sha256` column with a bound value. The
source is validated as a loopback HTTP origin before journal access, never
contacted by history, and omitted from its JSON. The original origin string
is hashed without normalization: an optional trailing slash produces a
different fingerprint. No journal migration, receipt conversion or resend
is performed. An origin fingerprint identifies the supplied address, not
an authenticated service instance; a later process can reuse that address.

H22 initially failed because the CLI did not recognize `--source`. After
implementation all 22 history tests passed. H20 exercises sparse matches
below 120 other-source rows, equal timestamps, all existing filters and
cursor continuation without database mutation. H21 instruments the database
boundary to ensure invalid origins are refused before access, including
redacted malformed-port diagnostics. H22 uses actual CLI subprocesses to
continue finished receipts and preserve `delivery-unknown`. D01 also reads
an actual AI-backed delivery receipt using the source predicate and verifies
that adding a slash finds no match.

All 167 unit tests passed on CPython 3.9.6, the independent reference HTTP
acceptance passed 19/19, and the evidence audit passed before committing.
These client checks are separate from backend reconstruction acceptance.

All 56 checks in the independent delivery smoke passed on CPython 3.9.6 /
SQLite 3.43.2 using today's actual AI inbox. The report binds the inbox,
client, CLI and tests to frozen source snapshots. A separate code review
found no blocking issues and independently passed the 22 history tests.
[Client report](2026-11-23-delivery-sources/python39.json),
[source manifest](2026-11-23-delivery-sources/manifest.json).

Today's single bounded contract-002 reconstruction used the existing
authenticated Codex CLI 0.166.0 with its clean default configuration. Its
first attempt passed 32/32 on CPython 3.9.6 / SQLite 3.43.2, using the
successful v1 AI candidate `20261103T023452Z-4ec8a9` for the migration fixture.
The CLI reported 106.445 generation seconds, 21,701 input tokens, 3,854 output
tokens and 91 reasoning output tokens. The resolved model and cost remain
unknown. [Reconstruction report](../evidence/20261123T023150Z-6d4fc7/report.json).

The separate handoff from the November 22 AI candidate to today's candidate
passed 8/8. Its predecessor creates the database via HTTP before the fixture
backdates synthetic timestamps; this is a controlled continuity observation,
not an elapsed retention-period claim. The bundle remains outside the AI
ledger and 32-check verdict.
[Continuity report](2026-11-23-continuity/report.json).
