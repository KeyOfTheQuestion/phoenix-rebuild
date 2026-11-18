# Filter notebook records by outcome · 2026-11-18

The notebook now filters by the ledger's exact status. Readers can choose
passed, acceptance failed, generation failed or verification error. The
selector combines with the AI-only filter and keeps the original order.
The headline counts use the full ledger. Changing languages preserves
the selection and translates the selector.

A local headless Chrome check first observed the missing control. After
implementation it compared record IDs for each outcome and the combined
AI filter, including after switching to Chinese. The Chromium suite also
uses a synthetic verification error to exercise its positive match
and verify exclusion from acceptance failures.

Review reproduced a startup error when cached HTML lacked the new selector.
The current script defaults to all outcomes when the selector is absent.
The new HTML requests a versioned script URL so it receives the corresponding
filter behavior. The browser suite covers the missing-selector case.
Local Chrome also loaded the previous commit's HTML with the current
script and rendered every record.

The backend regression passed 164 unit tests, 19 independent HTTP
checks and the evidence audit on CPython 3.9.6.

The contract-002 rebuild used the existing Codex CLI with its clean
default and passed all 32 checks on the first attempt. It ran on
CPython 3.9.6 with SQLite 3.43.2, using `20261103T023452Z-4ec8a9` for the
v1 migration fixture. The CLI reported 109.396 seconds of generation,
23,898 input tokens, 4,304 output tokens and 375 reasoning output
tokens. The resolved model and cost are unknown.
[Reconstruction report](../evidence/20261118T023139Z-cfc064/report.json).

The v2 handoff from `20261117T022946Z-78d1d1` to
`20261118T023139Z-cfc064` passed all eight checks. It reused the
predecessor's SQLite database after creating keyed events and retention
policies through HTTP and aging synthetic timestamps.
[Continuity report](2026-11-18-continuity/report.json).
