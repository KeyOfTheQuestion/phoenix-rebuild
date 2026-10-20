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

## Execution boundary

The generator returns code as structured text; the verifier runs it using a
temporary working directory and a minimal environment. This is normal local
execution of code, **not a security sandbox**. It can access files and network
resources allowed to the operating-system user. Evaluate only trusted candidates,
or run the entire repository inside a disposable VM/container without secrets.
Do not treat a Git worktree, a subprocess, or a prompt instruction as isolation.

The notebook is a static site. It runs no candidate code in visitors' browsers
and has no model API calls, registration, tracking, or server secret.

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
