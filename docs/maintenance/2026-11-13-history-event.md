# Filter delivery history by event ID · 2026-11-13

`delivery-history --event-id ID` traces local delivery attempts for one inbox
event. SQLite applies the exact event ID match together with the optional
receipt state and cursor position before the page limit. Reuse both filters
when following `next_cursor`. [Command documentation](../delivery.md#filter-delivery-history-by-event-id).

H15 finds three matching rows below 122 newer attempts for other events. It
checks case sensitivity and treats SQL-like ID text as a literal value. H16
combines both filters across pages through the CLI and checks a saved uncertain
outcome. H17 verifies refusal before journal access and redaction of invalid
input. D01 reads the finished receipt written by actual loopback delivery
using the AI inbox's event ID. The first focused test run observed the missing
CLI flag and API argument before implementation. All 17 history tests passed
after implementation.

The complete suite passed 158 unit tests and 19 independent HTTP checks on
CPython 3.9.6. The client smoke passed all 47 checks on CPython 3.9.6 with
SQLite 3.43.2 and CPython 3.14.6 with SQLite 3.53.3. Both reports bind the AI
inbox and the tested client files by SHA-256.
[Python 3.9 report](2026-11-13-delivery-sources/python39.json),
[Python 3.14 report](2026-11-13-delivery-sources/python314.json),
[source manifest](2026-11-13-delivery-sources/manifest.json).

The bounded contract-002 reconstruction used the existing Codex CLI with its
clean default. Its first attempt passed all 32 checks on CPython 3.9.6 with
SQLite 3.43.2, using `20261103T023452Z-4ec8a9` for the v1 migration fixture.
The CLI reported 101.807 seconds of generation, 22,999 input tokens, 3,996
output tokens and 26 reasoning output tokens. The resolved model and cost
are unknown. [Reconstruction report](../evidence/20261113T023101Z-6caeea/report.json).

The separate v2 handoff from `20261112T023638Z-af490a` to
`20261113T023101Z-6caeea` passed all eight checks. The predecessor created
policies and keyed events through HTTP. The fixture backdated synthetic event
timestamps and reopened the same SQLite database with the candidate.
[Continuity report](2026-11-13-continuity/report.json).
