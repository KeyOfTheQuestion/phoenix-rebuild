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

## Contract 002 reference pilot

The separate AI-assisted retention reference supports explicit per-inbox expiry
and atomic, opt-in migration from v1. It is not connected to AI generation or the
Docker runner. To run its focused/composed reference checks:

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
changed source bindings cannot produce a combined pass. AI generation and its
metrics stay on contract 001. See the
[composed verification and method change](maintenance/2026-10-28-retention-composed.md).

These are local subprocess checks of trusted code, not a hostile-code sandbox.
Migration data comes from the predecessor's HTTP API. After migration has been
checked, only synthetic v2 events are backdated directly in SQLite while stopped
to exercise expiry deterministically. The pilot reports this method explicitly,
does not call a model and does not publish AI attempts. Existing verification,
reconstruction remains on contract 001. See the
[complete pilot contract](../specs/webhook-inbox-retention.md) and
[verification record](maintenance/2026-10-27-retention-pilot.md).

`--full --publish` archives the exact entrypoint, all four project-module
dependencies, candidate, predecessor and contract used by the reference check.
It publishes a contract-002 reference row, not an AI attempt. Only full 32-check
mode supports publication. `python3 -m phoenix replay RUN_ID` verifies every
artifact and binding, then loads the frozen modules from temporary verified
copies. The current workspace verifier is not imported and the archive is not
written to. This currently requires trusted local execution; requesting
`--container` for a contract-002 archive exits 2 without host fallback.
See the [archive profile and verification](maintenance/2026-10-29-retention-archive.md).

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
