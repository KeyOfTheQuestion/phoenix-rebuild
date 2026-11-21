# Share a filtered notebook view · 2026-11-20

Implementation began on November 20 and remained uncommitted when that
session ended. It was resumed and completed for publication on November 21.
The November 20 reconstruction and continuity evidence retain their actual dates.

Readers can now share the page address after choosing a source and
outcome filter. `?kind=ai-rebuild&status=failed` opens the current
AI acceptance failures. The existing status names include `passed`,
`failed`, `generation-failed` and `verification-error`. Unknown
values fall back to the corresponding default.

Changing a filter replaces the current address's filter parameters.
Returning to all records removes those parameters. Evidence IDs, other
query parameters and fragments are retained. A published evidence
permalink still opens its report even when the selected filters exclude
the row. Filter links select the current ledger, so newly published
matching records can appear on later visits.

The local Chrome check initially failed because shared filters were
not restored. After implementation it restored a combined filter,
updated the address and reset to the full table. The browser suite
also checks a fresh context, reload, language switching, default
fallback and an evidence permalink outside the selected table.

Review reproduced an address/table mismatch after using an in-page
anchor, changing filters and navigating Back. The page now rereads the
filters on history navigation. The browser suite checks both Back
and Forward against the visible rows and controls.

The contract-002 rebuild used the existing Codex CLI and its clean
default. Its first attempt passed all 32 checks on CPython 3.9.6 with
SQLite 3.43.2. The v1 migration fixture used `20261103T023452Z-4ec8a9`.
The CLI reported 110.697 seconds of generation, 23,896 input tokens,
4,610 output tokens and 315 reasoning output tokens. The resolved
model and cost are unknown.
[Reconstruction report](../evidence/20261120T023740Z-b59799/report.json).

The separate November 20 v2 handoff from the November 19 candidate passed
8/8 ([continuity report](2026-11-20-continuity/report.json)). It remains
outside the reconstruction ledger and its 32-check verdict.

On November 21, a single bounded reconstruction cycle again used the clean
CLI default, the same v1 migration fixture and the installed CPython 3.9.6 /
SQLite 3.43.2 runtime. Its first attempt passed 32/32. The CLI reported
90.714 generation seconds, 23,914 input tokens, 4,152 output tokens and
198 reasoning output tokens; the resolved model and cost remain unknown.
[November 21 reconstruction report](../evidence/20261121T023358Z-835bd7/report.json).
The separate handoff from the November 20 AI candidate to this candidate
passed 8/8 ([continuity report](2026-11-21-continuity/report.json)).

Before committing on November 21, all 164 unit tests passed on Python 3.9,
the independent reference HTTP acceptance passed 19/19, and the evidence
audit passed. Local Chrome checked combined-filter URLs, reset, language
switching and Back/Forward navigation. The optional CI browser suite now
contains 22 checks, including three new shared-filter regressions; its
execution result is recorded in GitHub Actions rather than assumed here.
