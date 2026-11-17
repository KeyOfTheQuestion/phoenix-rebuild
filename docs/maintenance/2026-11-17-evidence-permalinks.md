# Share a notebook evidence record · 2026-11-17

Each evidence dialog now has a **Permalink** (永久链接) pointing to
`?run=RUN_ID` on the notebook. Opening it loads the published ledger,
matches the ID, and opens the corresponding evidence dialog. The link
works for AI and reference rows and uses the viewer's local language
preference. Closing the dialog leaves the ledger visible. Reloading the
permalink reopens that run.

The notebook asks for a report only after the ledger matches the exact ID.
An unknown ID shows a localized message and leaves the table available.
If a report returns a different ID, the viewer shows the report error.
The existing repository audit verifies archived artifact hashes.

The local headless Chrome check initially failed to open a known run.
After implementation it opened the exact AI report and showed the
unknown-run message for an invalid link. The Chromium browser suite
also exercises a fresh Chinese-language context, Escape and reload, and
checks that an unknown ID causes no report fetch before a valid row
is opened. Review caught an unknown-run notice that stayed in the
previous language after a switch. The notice now follows the language
toggle. Additional controls cover a report carrying the wrong ID and a
late response for a superseded selection.

The backend regression passed 164 unit tests, 19 independent HTTP
checks and the evidence audit on CPython 3.9.6.

The contract-002 rebuild used the existing Codex CLI with its clean
default. The first attempt passed all 32 checks on CPython 3.9.6 with
SQLite 3.43.2. The migration fixture used `20261103T023452Z-4ec8a9`.
The CLI reported 90.124 seconds of generation, 22,449 input tokens,
3,720 output tokens and 102 reasoning output tokens. The resolved model
and cost are unknown.
[Reconstruction report](../evidence/20261117T022946Z-78d1d1/report.json).

The separate v2 handoff from `20261116T023612Z-ba28c5` to
`20261117T022946Z-78d1d1` passed all eight checks. It reopened the
predecessor's SQLite database after creating keyed events and policies
through HTTP and aging synthetic event timestamps.
[Continuity report](2026-11-17-continuity/report.json).
