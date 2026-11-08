# Setup

## The dependency-free path

Clone this repository and use Python 3.9+. No installation step is required:

```sh
python3 -m phoenix verify
python3 -m phoenix audit
python3 -m phoenix serve
```

The notebook runs at http://127.0.0.1:8788. The reference service defaults to
http://127.0.0.1:8787 and needs a nonempty `PHOENIX_ADMIN_TOKEN`. Set
`PHOENIX_DB_PATH` and `PORT` to change its data file and local port.

## Local delivery history

Optional local delivery history uses no extra dependency: add `--journal
./deliveries.sqlite3` to `deliver-event`, then read it with `python3 -m phoenix
delivery-history --journal ./deliveries.sqlite3`. The directory must exist and
support hard links. Unconfirmed records are never retried by history. See the
[journal states and failure semantics](delivery.md#optional-local-attempt-journal).

## AI generation

Install the official [Codex CLI](https://developers.openai.com/codex/cli/),
authenticate it with `codex login`, and run:

```sh
python3 -m phoenix rebuild --attempts 2 --timeout 300
# Optional explicit selection; replace with a model available to your account:
python3 -m phoenix rebuild --model YOUR_AVAILABLE_MODEL
# Optional Docker acceptance after host-CLI generation:
python3 -m phoenix rebuild --container --verification-timeout 90
```

No API key is embedded in Phoenix. The CLI uses your existing authentication.
Generation consumes your provider's usage allowance and may incur charges under
your account's terms. Phoenix does not purchase credits or change subscriptions.
Each run is bounded to at most five attempts and has a per-attempt timeout.

The Python interpreter running Phoenix also launches its candidate/predecessor.
Use `python3.9 -m phoenix rebuild --attempts 2 --timeout 300` with an already
installed Python 3.9 to check the minimum supported version. No interpreter is
installed or silently substituted by Phoenix. New acceptance/replay JSON and
evidence dialogs show `execution_runtime` (Python implementation/version and
SQLite version); old missing metadata remains unknown. Replay measures the
current run and leaves the archived report intact. Inside Docker the observation
is measured in the container, not on the client. A single runtime pass is not
a compatibility matrix; see the [scope and verification](maintenance/2026-11-02-runtime-evidence.md).

On POSIX, compare an archived run using `python3 -m phoenix replay-matrix RUN_ID
--python python3.9 --python python3.14 --timeout 30`. Each name/path must refer
to an installed executable. Relative paths are made absolute before staging;
the selected symlink path is retained, including a virtual environment's Python.
The command accepts 1..4 invocations, defaults to 90 seconds per invocation and
allows 1..300 seconds. Timeout cleanup requests SIGTERM, then SIGKILL for that
invocation's process group, with separate bounded waits (1 and 5 seconds).
Remaining requested runtimes still run. Exit 0 means all passed, 1 means an
acceptance failure, and 2 means an input/archive refusal or any runtime/report
error. There is no installation or fallback. Private runtime diagnostics stay
in ignored `.phoenix/matrix-errors/`; public JSON uses ordinal runtime indices
and measured versions, never interpreter paths. Redirect JSON to a new file to
retain it; no ledger entry is published. See the [controls and limits](maintenance/2026-11-03-runtime-matrix.md).

By default the adapter passes `--ignore-user-config`. This prevents unrelated
MCP servers, settings, and unsupported configured model aliases from disrupting
the run. Authentication is still resolved by the CLI. To explicitly use your
personal provider/configuration, pass `--use-user-config`. No global configuration
is modified. Inspect ignored `.phoenix/generator-errors/latest.log` locally when
the generator fails. It is not automatically published.

With `--container`, Docker/image preparation must succeed before calling the
generator. `--timeout` bounds each generation attempt; `--verification-timeout`
bounds container execution separately (1..300 seconds, default 90). Generation
still uses the authenticated host CLI; only subsequent acceptance runs inside
Docker. Verifier and predecessor bytes are frozen before generation and are not
staged until it exits. A later runtime failure preserves the generated source,
prompt and provenance as `verification-error`, stops the cycle without another
model attempt, and exits 2. This means no acceptance verdict, rather than a failed
contract check. The notebook displays that distinction. Preflight refusal before
generation creates no reconstruction record. Default generation stays local.

## Execution boundary

Optional Docker verification is available with `python3 -m phoenix container-verify`.
It requires a Linux cgroup-v2 Docker engine and first-use registry access. It runs
the unchanged HTTP verifier and candidate inside a restricted, disposable
container with no host credentials forwarded and no external network. See the
[container setup and precise limits](container-runner.md). A missing runtime
returns exit code 2; it never silently executes the candidate on the host.

To repeat a trusted archived run without another model call:

```sh
python3 -m phoenix replay PUBLISHED_RUN_ID
python3 -m phoenix replay PUBLISHED_RUN_ID --container --timeout 90
```

Replay validates the ledger, manifest and recorded hashes before executing
archived code from verified bytes. Local replay uses temporary candidate/predecessor copies
and leaves the archive unchanged. Exit code 0 means acceptance passed, 1 means
acceptance failed, and 2 means the requested record was refused (including a
generation failure or inconsistent evidence). Hash consistency is not a security
sandbox or an independent signature.

`replay --container` uses the archived verifier and predecessor/fixture, rather
than the current harness used by `container-verify`. It passes only already
verified source bytes to Docker, does not load the archived verifier on the host,
and reports the record ID and hashes alongside its runtime observations.
An unavailable Docker runtime returns exit code 2 without host fallback.

The default generator returns code as structured text; local verification and replay without `--container` run it using a
temporary working directory and a minimal environment. This is normal local
execution of code, **not a security sandbox**. It can access files and network
resources allowed to the operating-system user. Evaluate only trusted candidates,
or run the entire repository inside a disposable VM/container without secrets.
Do not treat a Git worktree, a subprocess, or a prompt instruction as isolation.

The notebook is a static site. It runs no candidate code in visitors' browsers
and has no model API calls, registration, tracking, or server secret.

## Contract 002 generation and reference checks

Contract 002 supports explicit per-inbox expiry and atomic migration from v1,
with retention disabled initially. Generate it with the full 32-check frozen
acceptance package and a successful contract-001 predecessor:

```sh
python3 -m phoenix rebuild --contract 002 --attempts 2 --timeout 300 \
  --predecessor docs/evidence/20261103T023452Z-4ec8a9/server.py
```

The default remains contract 001 with local acceptance. For contract 002, add
`--container --verification-timeout 90` to preflight Docker before provider use,
then evaluate the generated source with the complete frozen package inside
Docker. Only the host CLI generates code; it receives no verifier/predecessor.
Later container/runtime report errors preserve the complete zero-check bundle
without host fallback or another model call. Completed AI archives support
local, Docker and runtime-matrix replay. See the
[container-generation controls](maintenance/2026-11-05-retention-container-rebuild.md). This 32-check fixture
still requires a v1 predecessor; use the separate continuity command below for a v2-to-v2 handoff.

The distinct `retention-ai-v1` profile includes the exact generation prompt and
all frozen dependencies. Generation failures retain zero-check bundles; local
evaluator errors retain generated source as `verification-error`, with no
acceptance verdict, mode switch or another model attempt. Private diagnostics
stay in ignored `.phoenix/generator-errors/latest-verification.log`. See the
[actual generation, method change and checks](maintenance/2026-11-04-retention-ai.md).

The separate AI-assisted reference makes no model call. To run its checks:

```sh
python3 -m phoenix verify-retention
python3 -m phoenix verify-retention --full
python3 -m phoenix verify-retention --full --publish
python3 -m phoenix verify-retention --predecessor docs/evidence/RUN_ID/server.py
```

The default is the original 13-check pilot. `--full` composes it with 19 baseline
checks that explicitly expect schema version 2. The result needs all 32 distinct
check IDs to pass. Candidate/predecessor bytes are captured once for both phases;
each phase creates its own predecessor-populated database. Incomplete checks or
changed source bindings cannot produce a combined pass. These reference checks
remain separate from AI generation and its metrics. See the
[composed verification and method change](maintenance/2026-10-28-retention-composed.md).

These are local subprocess checks of trusted code, not a hostile-code sandbox.
Migration data comes from the predecessor's HTTP API. After migration has been
checked, only synthetic v2 events are backdated directly in SQLite while stopped
to exercise expiry deterministically. The pilot reports this method explicitly,
does not call a model and does not publish AI attempts. Default baseline
verification and generation remain on contract 001. See the
[complete pilot contract](../specs/webhook-inbox-retention.md) and
[verification record](maintenance/2026-10-27-retention-pilot.md).

`--full --publish` archives the exact entrypoint, all four project-module
dependencies, candidate, predecessor and contract used by the reference check.
It publishes a contract-002 reference row, not an AI attempt. Only full 32-check
mode supports publication. `python3 -m phoenix replay RUN_ID` verifies every
artifact and binding, then loads the frozen modules from temporary verified
copies. The current workspace verifier is not imported and the archive is not
written to. Add `--container --timeout 90` for a contract-002 archive to execute
its full verified dependency package inside Docker, with no archived code loaded
on the host. All eight source/contract hashes, the 32-check profile and boundary
observations must match. Docker remains optional; runtime failures exit 2 without
host fallback. See the [archive profile](maintenance/2026-10-29-retention-archive.md)
and [container replay checks](maintenance/2026-10-30-retention-container.md).

## V2-to-v2 policy and data continuity

Choose trusted candidate and predecessor files that both implement contract 002:

```sh
python3 -m phoenix verify-continuity --candidate path/to/trusted/v2.py \
  --predecessor path/to/trusted/previous-v2.py --output-dir path/to/new-continuity-bundle
python3 -m phoenix replay-continuity path/to/new-continuity-bundle
```

`verify-continuity` runs eight separate HTTP checks of existing policies, event
data and idempotency across handoff, restart, explicit expiry and expired-key reuse.
It creates enabled, explicitly disabled and longer-policy inboxes through the v2
predecessor. Synthetic timestamps are backdated through SQLite only while stopped,
then observed again through predecessor HTTP before handoff. This does not claim
that real time elapsed.

Omit `--output-dir` to print the report without saving a bundle. With a new output
directory, the command saves five frozen inputs (candidate, predecessor, contract,
continuity verifier and HTTP helper), plus `report.json` and `manifest.json`.
Replay checks the complete file set, hashes, kind/profile and source bindings
before loading code; it executes verified temporary copies and leaves the bundle
unchanged. Preserve failed observations as well as passes.

Both commands are local-only trusted-code execution with normal user permissions.
They make no model calls and add no AI ledger rows. The eight checks do not replace
or extend the existing 32-check reconstruction/migration fixture, which continues
to require a v1 predecessor. See the [fixture, evidence and limits](maintenance/2026-11-06-v2-continuity.md).


## Explicit loopback delivery

Run `python3 -m phoenix deliver-event --source http://127.0.0.1:8787 --event-id
EVENT_ID --destination http://127.0.0.1:9000/webhook --timeout 3` after starting
both local services. Set `PHOENIX_ADMIN_TOKEN` to the inbox's existing admin
token. The token travels to the worker through stdin and is used only for the
source GET. Exit 0 records a 2xx receiver response, 1 records another receiver
status, and 2 records refusal, source failure or an uncertain delivery.

The overall worker deadline covers startup and both HTTP operations. A POST
that has started may have reached the receiver even if the deadline expires.
The receipt preserves that uncertainty. See [bounds and receipt semantics](delivery.md).

## Publish the notebook with GitHub Pages

The included Pages workflow publishes `docs/` on pushes to `main`. Enable Pages
with GitHub Actions as the build source. The published URL is:
https://keyofthequestion.github.io/phoenix-rebuild/

Verification and evidence integrity checks run before publication. AI rebuilds
are initiated on a trusted local machine, not on public pull-request jobs with
an account key. The normal CI requires no model credentials.

## Development

```sh
python3 -m unittest discover -s tests -v
python3 -m compileall -q phoenix examples/reference
python3 -m phoenix verify
python3 -m phoenix audit
```

Reconstruction results are evidence: keep historical attempts even when improving
the harness. Describe material method changes in the changelog, and give each run
its own contract and verifier snapshots.

## Notebook browser checks

These optional development tools require Python 3.10+; the following example
uses Python 3.12 in a local, ignored environment. The reference service and
independent HTTP harness continue to need only Python 3.9+ and its standard
library.

```sh
python3.12 -m venv .phoenix/browser-env
.phoenix/browser-env/bin/python -m pip install -r requirements-browser.txt
.phoenix/browser-env/bin/python -m playwright install --only-shell chromium
.phoenix/browser-env/bin/python tests/browser_smoke.py
```

On Linux, use `install --with-deps --only-shell chromium` to install Chromium's
system libraries as well. See the official [Playwright browser installation guide](https://playwright.dev/python/docs/browsers).
The pinned Playwright package and matching browser binary are test dependencies;
the public site still has no frontend build step or browser automation dependency.

The suite starts and stops its own local HTTP server on an assigned free port,
uses fresh browser contexts, and checks the real site under a project subpath.
It covers ledger counts, filtering, language persistence, every record's dialog
and artifact URLs, contract navigation, and failed ledger/report loading.
`python3 -m unittest discover -s tests -v` keeps running only the dependency-free
unit checks. Run `tests/browser_smoke.py` explicitly for browser coverage.
CI runs both suites; the existing Pages workflow also requires both to pass.
