# Filter delivery history by state · 2026-11-12

`delivery-history --state unconfirmed` finds attempts that have yet to save a
receipt. `--state finished` selects any saved outcome, including an uncertain
delivery result. The filter runs before the page limit. Continue with the same
state and the returned `next_cursor`. A finalized attempt leaves the
unconfirmed list when the next page is read. [Command documentation](../delivery.md#filter-delivery-history-by-state).

The tests cover three unconfirmed rows older than 122 finished rows, cursor
continuation after a receipt is saved, and refusal of an invalid state before
opening the journal. The client smoke also filters for a finished receipt
written by actual loopback delivery.

The first full test run overlapped the daily rebuild. The continuity test
`test_source_paths_can_change_after_capture_without_changing_the_execution_or_bundle`
failed its ledger byte comparison while the rebuild prepended today's entry.
The local failure log is retained. The subsequent verification ran in sequence
after the rebuild finished. It passed 155 unit tests and all 19 independent
HTTP checks on CPython 3.9.6. The initial log has SHA-256
`b91186490249c7e750791f26802c01d6d0edbb56296bde4328e5ca2da2cd8bf2`.

The client smoke passed all 44 checks on CPython 3.9.6 with SQLite 3.43.2 and
on CPython 3.14.6 with SQLite 3.53.3. Both reports bind the tested AI inbox and
client sources by SHA-256. [Python 3.9 report](2026-11-12-delivery-sources/python39.json), [Python 3.14 report](2026-11-12-delivery-sources/python314.json), [source manifest](2026-11-12-delivery-sources/manifest.json).

The bounded reconstruction used the existing Codex CLI and its clean default.
The first attempt passed 32 of 32 checks on CPython 3.9.6 with SQLite 3.43.2.
The v1 migration fixture used `20261103T023452Z-4ec8a9`. The CLI reported
86.973 seconds of generation, 22,997 input tokens, 3,733 output tokens and
74 reasoning output tokens. The resolved model and cost are unknown.
[Reconstruction report](../evidence/20261112T023638Z-af490a/report.json).

The eight-check v2 handoff from `20261111T023555Z-eda255` to
`20261112T023638Z-af490a` passed. It used the predecessor's HTTP API to create
retention policies and keyed events and aged synthetic event timestamps before
reopening the same database with the candidate. [Continuity report](2026-11-12-continuity/report.json).
