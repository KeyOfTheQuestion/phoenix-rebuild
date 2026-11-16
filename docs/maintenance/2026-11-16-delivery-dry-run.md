# Check a delivery before sending · 2026-11-16

`deliver-event --dry-run` fetches the event and validates the original
UTF-8 body, the selected headers, and any supplied body digest and
destination policy. A successful receipt reports `ready`, zero POST
attempts and a null HTTP status. The worker stops before opening a receiver
connection. A dry run cannot create an attempt journal. A subsequent send
fetches the event again. [Command documentation](../delivery.md#check-a-delivery-without-sending-it).

D01 validates an event from the archived AI inbox before dispatching it
in a separate invocation. D15 exercises the CLI with a matched policy,
a digest mismatch, a refused policy, and a journal argument. It observes
two source requests and zero receiver requests. D16 injects incomplete or
contradictory worker receipts to verify that a dry run cannot report a
successful delivery and a normal invocation cannot report `ready`.
The two new tests initially failed because the flag and API argument were
missing. All 16 delivery tests passed after implementation.

Review found that a parent loaded from the current version could still
pass a usable receiver URL to an older worker after a checkout switch.
That worker ignored the new dry-run field. The client now omits the
receiver URL from dry-run worker input after validating it in the parent.
D16 verifies omission for dry runs and inclusion for normal sends.
The previous worker from `8384eb4` refused the updated input before
constructing an HTTP connection in a separate mocked-transport check.

One local full-suite rerun returned no JSON from the existing matrix timeout
fixture `test_timeout_terminates_the_runtime_process_group_and_continues`.
The failure log is retained locally with SHA-256
`7da2c450f2d71148057d8968d0b662e44217ceb387e6ef8d320c1abb81f4a651`.
Three isolated reruns returned the expected JSON and passed. The cause of
the intermittent failure was not established.

The final full suite passed 164 unit tests and 19 independent HTTP
checks on CPython 3.9.6. All 53 delivery checks passed on CPython 3.9.6
with SQLite 3.43.2 and on CPython 3.14.6 with SQLite 3.53.3. The reports
bind the tested AI inbox and client sources by SHA-256.
[Python 3.9 report](2026-11-16-delivery-sources/python39.json),
[Python 3.14 report](2026-11-16-delivery-sources/python314.json),
[source manifest](2026-11-16-delivery-sources/manifest.json).

The bounded contract-002 reconstruction used the existing Codex CLI with
its clean default and passed all 32 checks on the first attempt. It ran on
CPython 3.9.6 with SQLite 3.43.2, using `20261103T023452Z-4ec8a9` for the
v1 migration fixture. The CLI reported 83.866 seconds of generation, 22,461
input tokens, 3,768 output tokens and 108 reasoning output tokens. The
resolved model and cost are unknown.
[Reconstruction report](../evidence/20261116T023612Z-ba28c5/report.json).

The v2 handoff from `20261115T023552Z-518725` to
`20261116T023612Z-ba28c5` passed all eight checks. The predecessor created
events and policies through HTTP. The fixture backdated synthetic events
before reopening the same SQLite database with the new candidate.
[Continuity report](2026-11-16-continuity/report.json).
