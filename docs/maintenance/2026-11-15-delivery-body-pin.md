# Pin the expected delivery body digest · 2026-11-15

`deliver-event --expect-body-sha256 DIGEST` checks a caller-supplied SHA-256
against the exact UTF-8 body before the worker enters the POST phase. This
gives operators a way to check against a digest obtained independently of the
delivery fetch. A mismatch returns `source-error` and zero POST attempts.
The final receipt records the expected digest and the observed digest when
available. [Command documentation](../delivery.md#pin-the-body-digest).

D01 pins the digest of a synthetic event delivered from the archived AI
inbox to an independent receiver. D13 serves a replacement body with a
matching source-provided digest and checks that the receiver records zero
requests. It verifies CLI exit 2 and the retained journal receipt. D14
refuses malformed digests before network or journal activity. The first
focused run failed because the CLI flag and keyword argument were absent.
All 14 delivery tests passed after implementation.

Review found that the parent could accept a malformed worker completion
after a POST marker even when the observed digest was missing or differed
from the expected digest. D12 reproduced both cases. The parent now
retains the marker and reports an unknown outcome if that binding fails.
Both failure controls and the full delivery suite were rerun.

The final suite passed 162 unit tests and 19 independent HTTP checks on
CPython 3.9.6. All 51 delivery smoke checks passed on CPython 3.9.6 with
SQLite 3.43.2 and on CPython 3.14.6 with SQLite 3.53.3. Both reports bind
the tested AI inbox and client sources by SHA-256.
[Python 3.9 report](2026-11-15-delivery-sources/python39.json),
[Python 3.14 report](2026-11-15-delivery-sources/python314.json),
[source manifest](2026-11-15-delivery-sources/manifest.json).

The bounded contract-002 reconstruction used the existing Codex CLI with
its clean default. The first attempt passed all 32 checks on CPython 3.9.6
with SQLite 3.43.2. The v1 migration fixture used `20261103T023452Z-4ec8a9`.
The CLI reported 79.392 seconds of generation, 22,467 input tokens, 3,651
output tokens and 94 reasoning output tokens. The resolved model and cost
are unknown. [Reconstruction report](../evidence/20261115T023552Z-518725/report.json).

The separate v2 handoff from `20261114T023215Z-093277` to
`20261115T023552Z-518725` passed all eight checks. The predecessor created
keyed events and retention policies through HTTP. The fixture backdated
synthetic event timestamps and reopened the same SQLite database with the
new candidate. [Continuity report](2026-11-15-continuity/report.json).
