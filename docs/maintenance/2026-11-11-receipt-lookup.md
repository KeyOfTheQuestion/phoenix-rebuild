# Delivery receipt lookup · 2026-11-11

`delivery-receipt --journal FILE --attempt-id ID` reads a single journal attempt
by the `journal_attempt_id` returned by delivery. This lets an operator inspect
an older receipt without walking history pages. An unconfirmed attempt returns
`receipt: null`, and a finished attempt returns the saved delivery outcome.
The command exits 0 for a found row, 1 for an absent row, and 2 for invalid input
or an unreadable journal. [Command documentation](../delivery.md#look-up-a-delivery-receipt).

The three added checks look up an attempt among 123 synthetic rows, refuse
malformed identifiers before journal access, and verify the three CLI exit
codes. The real loopback delivery check also reads the newly saved attempt
by ID and compares it with the history result. Journal byte comparisons
verify read-only access.

CPython 3.9.6 passed 152 unit tests and the reference implementation passed all
19 independent HTTP checks. The client suite passed 41 checks on CPython 3.9.6
with SQLite 3.43.2 and on CPython 3.14.6 with SQLite 3.53.3. Both reports bind the
actual AI inbox and the client sources by SHA-256. [Python 3.9 report](2026-11-11-delivery-sources/python39.json), [Python 3.14 report](2026-11-11-delivery-sources/python314.json), [source manifest](2026-11-11-delivery-sources/manifest.json).

The single bounded Codex CLI reconstruction passed all 32 checks on attempt 1
using CPython 3.9.6 and the clean CLI default. The 32-check migration fixture
used `20261103T023452Z-4ec8a9` as its v1 predecessor. The report records
93.948 seconds of generation, 22,451 input tokens, 3,884 output tokens and
137 reasoning output tokens. Resolved model identity and cost remain unknown.
[Reconstruction report](../evidence/20261111T023555Z-eda255/report.json).

The eight-check v2 continuity fixture passed from yesterday's successful
`20261110T025401Z-6dcf54` to today's `20261111T023555Z-eda255`. It created
policies and keyed events over HTTP with the predecessor, aged synthetic event
timestamps, and verified the receiving implementation against that database.
[Continuity report](2026-11-11-continuity/report.json).

## CI failure and follow-up

The first CI run passed 40 of 41 delivery checks on CPython 3.9.25.
K09 failed in the descriptor count check. The smoke report gives the failing
check and its duration. [CI run](https://github.com/KeyOfTheQuestion/phoenix-rebuild/actions/runs/42947514237), [retained report](2026-11-11-ci-delivery-failure.json).

The old test counted all process descriptors alongside HTTP fixture threads.
Closing an unrelated descriptor during policy validation reproduced a false
failure with counts of 9 before and 8 after. The CI report leaves the particular
descriptor change unconfirmed. The revised test exercises the actual policy
reader in its own process. K08 covers delivery refusal before HTTP access.

The revised test passed while an unrelated descriptor closed in the parent.
Suppressing descriptor closes in the child produced 45 open descriptors from
an initial 5, and the revised test caught that injected leak. [Follow-up client report](2026-11-11-delivery-recheck.json).
