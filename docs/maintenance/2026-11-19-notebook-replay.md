# Replay a record from its evidence dialog · 2026-11-19

Replayable records display a command with their complete published ID.
Readers can copy it from the evidence dialog and run it from a current
checkout with Python 3.9+. The command verifies the archive and executes
its captured candidate and evaluator using local permissions. Its output
describes a new observation on that runtime.

The browser exposes the command for a safe ID, a verifier SHA-256, and
a passed, failed or verification-error status. A generation failure has
no executable candidate and its dialog clears the command. A cached page
without the new section can still open evidence. The browser never
executes a replay command.

A local Chrome check first reported the missing replay control, then
verified the exact command for a passing run and its absence for a
generation failure. The browser suite checks every published record,
Chinese instructions, clearing across successive dialogs, and refusal
of shell-like IDs in synthetic responses.

The backend suite passed 164 unit tests, 19 independent HTTP checks
and the evidence audit on CPython 3.9.6. The exact command displayed
for today's run was executed and passed 32/32 using the archived
evaluator. [Actual replay report](2026-11-19-replay.json).

The contract-002 rebuild used the existing Codex CLI and its clean
default. Its first attempt passed all 32 checks on CPython 3.9.6 with
SQLite 3.43.2. The v1 migration fixture used `20261103T023452Z-4ec8a9`.
The CLI reported 108.631 seconds of generation, 23,900 input tokens,
4,433 output tokens and 419 reasoning output tokens. The resolved
model and cost are unknown.
[Reconstruction report](../evidence/20261119T023616Z-0bbb0b/report.json).

The v2 handoff from `20261118T023139Z-cfc064` to
`20261119T023616Z-0bbb0b` passed all eight checks. It reused the
predecessor's SQLite database after creating keyed events and policies
through HTTP and aging synthetic timestamps.
[Continuity report](2026-11-19-continuity/report.json).
