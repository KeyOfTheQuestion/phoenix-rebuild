# Filter delivery history by receiver URL · 2026-11-14

`delivery-history --destination URL` traces attempts for one exact receiver
URL. The reader validates the same IPv4 loopback HTTP format used for delivery,
hashes the input in memory and queries the stored destination fingerprint. The
filter combines with the event ID, receipt state and cursor before the page
limit. The journal already records the required fingerprint, so this query
also works with existing journals. [Command documentation](../delivery.md#filter-delivery-history-by-receiver-url).

H18 pages three matching rows beyond 120 newer attempts for a different
receiver. It combines all filters, checks query-string sensitivity and verifies
that reading preserves the journal bytes. H19 checks early refusal of malformed
URLs and redaction of the input. D01 reads a receipt from an actual loopback POST
by its receiver, event and saved state. Before implementation the two new
focused checks failed because the CLI flag and API argument were missing.
All 19 history checks passed after implementation.

Review found that a malformed port could appear in the Python exception
chain even though the CLI diagnostic was redacted. The added traceback
assertion reproduced the leak with synthetic text. The history reader now
suppresses the underlying parser traceback, and H19 checks both surfaces.

The final suite passed 160 unit tests and 19 independent HTTP checks on
CPython 3.9.6. The client smoke passed all 49 checks on CPython 3.9.6 with
SQLite 3.43.2 and CPython 3.14.6 with SQLite 3.53.3. Both reports bind the AI
inbox and tested client sources by SHA-256.
[Python 3.9 report](2026-11-14-delivery-sources/python39.json),
[Python 3.14 report](2026-11-14-delivery-sources/python314.json),
[source manifest](2026-11-14-delivery-sources/manifest.json).

The bounded contract-002 reconstruction used the existing Codex CLI with its
clean default. The first attempt passed all 32 checks on CPython 3.9.6 with
SQLite 3.43.2. It used `20261103T023452Z-4ec8a9` for the v1 migration fixture.
The CLI reported 82.525 seconds of generation, 22,455 input tokens, 3,741
output tokens and 106 reasoning output tokens. The resolved model and cost
are unknown. [Reconstruction report](../evidence/20261114T023215Z-093277/report.json).

The separate handoff from the November 13 AI candidate
`20261113T023101Z-6caeea` passed all eight v2 continuity checks. The predecessor
created keyed events and retention policies through HTTP. The fixture
backdated synthetic event timestamps before reopening the same SQLite
database with the new candidate.
[Continuity report](2026-11-14-continuity/report.json).
