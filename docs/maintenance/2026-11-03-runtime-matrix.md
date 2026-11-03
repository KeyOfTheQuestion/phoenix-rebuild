# 2026-11-03: bounded replay across installed Python runtimes

`replay-matrix` now checks one frozen archive with 1..4 explicitly selected,
already-installed Python executables on POSIX. It closes the installed-runtime
matrix follow-up in roadmap item 9. It makes no model call, installs no runtime,
does not publish ledger entries and never changes historical outcomes.

```sh
python3 -m phoenix replay-matrix 20261103T023452Z-4ec8a9 \
  --python /usr/bin/python3 --python python3 --timeout 30
```

The names above selected the installed CPython 3.9.6 / SQLite 3.43.2 and
CPython 3.14.6 / SQLite 3.53.3 on this host. They are measured observations,
not version claims inferred from executable names. Public results identify
invocations by ordinal index and actual runtime fields; private interpreter
paths and raw diagnostics are not included.

## Actual positive and negative controls

| Frozen archive | Python 3.9.6 | Python 3.14.6 | Matrix verdict |
| --- | --- | --- | --- |
| [Today's successful candidate](2026-11-03-matrix-passing.json) | 19/19 passed | 19/19 passed | Passed; exit 0 |
| [October 30 candidate](2026-11-03-matrix-negative.json) | 17/19 passed; overall failed | 19/19 passed | Failed; exit 1 |

The negative control reproduces the previously disclosed C06/C08 failure on
Python 3.9: strict parsing of an empty query causes HTTP 400 on event-list
requests. It retains both fresh results, including the passing runtime, without
converting the original archived pass into a different historical observation.
These matrix invocations neither regenerate source nor add AI attempts.

## Source and verdict handling

The command audits the ledger/manifest/report and captures source bytes once,
before any requested runtime starts. Each invocation receives new temporary
copies of those same verified bytes, preserving the recorded predecessor or
early SQL fixture. Contract-002 archives include their complete frozen helper
package. No current workspace verifier is substituted. The worker uses `-I -B`
and loads the archived verifier inside the requested child process; its own
source SHA-256 is included as `matrix_worker_sha256`.

Parent validation checks candidate, predecessor, verifier and contract hashes,
plus all four helper bindings for contract 002. It requires measured runtime
metadata, consistent check counts/IDs and the complete passing 19/32-check set.
A failed acceptance report remains a failure; an invalid report receives no
acceptance verdict. Missing, broken, timed-out or invalid-output runtimes are
listed as `runtime-error`; no other Python is silently substituted.

Aggregate counts describe requested invocations, not unique Python versions,
new AI attempts or additional backend checks. Exit 0 means all invocations
passed; exit 1 means acceptance failed with no runtime error; exit 2 means an
archive/input refusal or at least one runtime/report error. Every requested
runtime still receives a result after another runtime fails. Refused archives
are rejected before execution.

The default deadline is 90 seconds per invocation, configurable from 1 to 300;
at most four invocations are accepted. Each gets its own POSIX process group.
On timeout, cleanup requests SIGTERM and then SIGKILL for that group, with
separate bounded waits of 1 and 5 seconds. The timeout regression starts a real
sleeping descendant, verifies it is stopped and confirms the next runtime still
runs. Relative executable paths are made absolute before temporary staging,
while retaining the selected symlink path so virtual-environment selection is
not bypassed. Private diagnostics stay under ignored `.phoenix/matrix-errors/`.

This is local execution with normal user permissions and network access.
`-I`, temporary directories and process-group cleanup are not a hostile-code
sandbox, interpreter attestation or CPU/memory/filesystem isolation. A malicious
subject could interfere with its cooperative evaluator or escape ordinary
cleanup assumptions. POSIX is required; Windows and a container runtime matrix
are outside this command's current scope. Two measured versions do not establish
compatibility with all Python/SQLite releases.

## Verification

- Python 3.9.6: full dependency-free suite, **86 passed**, 42.563 seconds.
- Python 3.14.6: full dependency-free suite, **86 passed**, 19.800 seconds.
- Independent reference HTTP acceptance: **19/19 passed**.
- Public evidence audit, Python compilation and notebook JavaScript syntax: passed.
- Nine new matrix controls cover actual archived v1/v2 and early SQL-fixture
  execution, immutable archives/ledger, paths changing after audit, pre-execution
  tamper refusal, mixed pass/fail aggregation, malformed results, missing runtimes,
  invocation/deadline limits, preserved interpreter symlinks and timeout cleanup.
  Synthetic runtimes and fixture ledgers are temporary and never published as AI runs.
- The actual local smoke replays v1/v2 archives through the new command using
  the installed Python 3.9.6. The existing Python 3.9/3.12/3.13 CI jobs and Pages
  workflow also run `tests/matrix_smoke.py`, checking actual measured versions,
  19/32-check success, source bindings and unchanged archive/ledger contents.
  Its marker is `RUNTIME_MATRIX_EVIDENCE=`; no model credentials are used.

This host still has no Docker or optional Playwright installation. Real remote
Docker and browser outcomes are reported by the existing workflows; no local
Docker/Chromium run is claimed. Existing SQLite cleanup warnings in newer Python
tests are not represented as warning-free behavior or repaired by this feature.
The backend contracts, reference implementations and frozen verifier source
remain unchanged today.

## Today's one bounded real generation cycle

The daily command used the installed system Python 3.9.6, existing authenticated
`codex-cli 0.145.0`, clean default configuration, two attempts at most and a
300-second limit each. Both attempts used the latest successful AI program,
`20261102T020351Z-0ba772`, to create old data through real HTTP. Its source hash
is `9302b4710f7381addb3905ac7d9ba8806e27a6a294cf902137bce4b48e42e3f3`.

| Actual attempt | Result | Generation seconds | Observed input/output/reasoning tokens | Cached input tokens |
| --- | --- | --- | --- | --- |
| [`20261103T023415Z-9c296c`](../evidence/20261103T023415Z-9c296c/report.json) | 17/19; failed C06/C08 | 39.254 | 17812 / 4447 / 516 | 0 |
| [`20261103T023452Z-4ec8a9`](../evidence/20261103T023452Z-4ec8a9/report.json) | 19/19 passed | 36.657 | 17640 / 4143 / 457 | 10752 |

The second attempt received only the failed-check summaries, not the previous
source; it is feedback-assisted, not an independent one-shot sample. The first
source SHA-256 is `d9eba954a6ae86292793fc04af808392bca38513ce53865890329c38d23c7514`;
the second is `e2b2f0485fd8ebccee134abe2aea93c35046bfb5e25d75ac287ad16442da22d3`.
Both used the unchanged verifier
`494d66e9c3e66b33596ebb0ba55e68d3db32347bc1e658c3bc1f0a87f3ebe0b1`.
Both report CPython 3.9.6 / SQLite 3.43.2, zero observed tool calls, no manual
candidate edits and zero cache-write input tokens. Actual acceptance began at
02:34:14.983116 UTC and 02:34:52.286416 UTC on November 3 respectively.
Resolved model identity and monetary costs remain unknown. No third model call,
model/account override, credential creation or credit purchase was made.

The notebook and English/Chinese README link these observations; the failed
attempt remains visible. No success streak, model ranking or full compatibility
claim is derived from this bounded sample.
