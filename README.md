<p align="center"><img src="docs/mark.svg" width="64" alt="Phoenix"></p>

<h1 align="center">Phoenix</h1>

<p align="center"><strong>A backend. Born again.</strong><br>Same contract. Fresh code. Old data. Public evidence.</p>

<p align="center"><a href="https://keyofthequestion.github.io/phoenix-rebuild/">Experiment notebook</a> · <a href="README.zh-CN.md">中文</a> · <a href="specs/webhook-inbox.md">The contract</a> · <a href="docs/methodology.md">Methodology</a></p>

[![CI](https://github.com/KeyOfTheQuestion/phoenix-rebuild/actions/workflows/ci.yml/badge.svg)](https://github.com/KeyOfTheQuestion/phoenix-rebuild/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-dc582b.svg)](LICENSE)
[![Python 3.9+](https://img.shields.io/badge/Python-3.9%2B-35745b.svg)](pyproject.toml)

**Can AI rebuild the same useful backend from its written contract—and still read the previous implementation's data?**

Phoenix is an open, executable experiment. A generator receives a public specification in a fresh directory, produces a complete implementation, and faces an independent HTTP acceptance harness. Every published attempt includes its source, exact contract, outcome, artifact hashes, and reported usage. Failed attempts stay visible.

The first subject is a **durable webhook inbox**: capture exact UTF-8 bodies, authenticate inspection routes, deduplicate concurrent deliveries, preserve credentials, and reopen an existing SQLite database. It has 19 acceptance checks. There is a runnable, human-reviewed reference implementation; reference checks are labeled separately from AI rebuilds.

## Try it in a minute

Python 3.9+ and Git. No runtime dependencies or API key needed to verify the reference.

```sh
git clone https://github.com/KeyOfTheQuestion/phoenix-rebuild.git
cd phoenix-rebuild
python3 -m phoenix verify
python3 -m phoenix audit
python3 -m phoenix serve
# open http://127.0.0.1:8788
```

## Rebuild it for real

Install and authenticate the [Codex CLI](https://developers.openai.com/codex/cli/), then:

```sh
python3 -m phoenix rebuild --attempts 2 --timeout 300
# To check compatibility with a specific trusted earlier AI implementation:
python3 -m phoenix rebuild --predecessor docs/evidence/RUN_ID/server.py
# Optional: keep generation on the host CLI, then verify the result in Docker:
python3 -m phoenix rebuild --container --verification-timeout 90
```

The adapter makes actual model calls using your CLI authentication and usage allowance. It starts with a clean CLI configuration, preserves authentication, stages **no reference source or verifier**, and requests a structured, complete Python program. Each candidate gets a new directory; retries receive only failure summaries, not the previous source. Supply `--model <available-model>` to select a model or `--use-user-config` to opt into your personal CLI configuration.

Generation never deletes repository code. Only disposable temporary directories are discarded. Evidence is saved under `docs/evidence/` before cleanup. Rebuild and replay without `--container` run candidates as local child processes with a minimal environment; this is **not an adversarial-code sandbox**. See [execution boundaries](docs/setup.md) before evaluating code you do not trust.

## Inspect the proof

```text
specs/webhook-inbox.md          the implementation contract
examples/reference/server.py   the useful reference service
phoenix/verify.py              independent HTTP acceptance harness
phoenix/rebuild.py             actual Codex CLI generation adapter
docs/runs.json                public attempt ledger
docs/evidence/<run>/           code, contract, checks, provenance, hashes
```

For current records, the verifier first starts the previous implementation, creates an inbox and event **through real HTTP**, stops it, then starts the candidate against the same database. A successful compatibility check means the old event and its idempotency state survived. Early records explicitly use a verifier-created schema fixture instead; inspect `compatibility_fixture` or the archived verifier.

```sh
python3 -m phoenix replay <published-run-id>
python3 -m phoenix replay <published-run-id> --container --timeout 90
python3 -m phoenix verify --candidate path/to/trusted/server.py
```

Replaying first checks the ledger, manifest and artifact hashes, then runs a temporary copy of the verified candidate, verifier and predecessor (when recorded). It refuses inconsistent bundles and generation failures with exit code 2, before loading archived code, and does not write into the evidence directory. It does not regenerate code or charge model usage. See the [replay integrity repair and verification](docs/maintenance/2026-10-21-replay-integrity.md).

Passing is evidence for these checks and this contract—not production certification, model superiority, or guaranteed correctness. Artifact hashes detect inconsistent files; they are not an independent signature. USD cost is unknown and is never estimated from invented prices.

## Verify inside an optional container

With a Linux cgroup-v2 Docker engine, `python3 -m phoenix container-verify` runs the same HTTP checks in a disposable container with external networking disabled, a read-only root/input mount, no host credentials forwarded, and explicit CPU, memory and PID limits. An actual boundary probe must confirm the profile before a report is accepted. The image is pinned by digest, and an unavailable runtime produces an error without host fallback.

`container-verify` uses the current harness. For an archived experiment, `replay RUN_ID --container` checks the bundle first and sends the exact verified candidate, archived verifier and recorded predecessor bytes to Docker, without loading that verifier on the host. Early records retain their original SQL fixture when no predecessor was recorded. These verification commands do not rewrite historical outcomes or call a model.

`rebuild --container` makes a real model call through the host CLI, then verifies the generated source using verifier/predecessor snapshots frozen before generation. Docker and the pinned image are checked before any model call. A later runtime failure preserves the generated code as `verification-error`, with no acceptance verdict, host fallback or generation retry; exit code 2 distinguishes this from an acceptance failure. `--timeout` still limits generation; `--verification-timeout` separately limits container execution. The default remains local, and the scheduled reconstruction on this host remains local because Docker is unavailable here.

See the [container profile, limits and setup](docs/container-runner.md) and [2026-10-26 container rebuild verification record](docs/maintenance/2026-10-26-container-rebuild.md).

## Pilot: retention and v1 migration

[Contract 002](specs/webhook-inbox-retention.md) adds per-inbox retention, explicit batches of at most 100 expired-event deletions, and atomic migration from v1. Existing events, tokens and idempotency survive migration; retention starts disabled. The AI-assisted [reference pilot](examples/retention/server.py) keeps the original tables and adds a policy table. Policy updates and reads never delete events implicitly.

```sh
python3 -m phoenix verify-retention
# Exercise migration from a trusted archived v1 AI implementation:
python3 -m phoenix verify-retention --predecessor docs/evidence/RUN_ID/server.py
```

The separate 13-check pilot uses real predecessor HTTP data and checks migration, policy persistence, bounded/concurrent expiry, isolation and key reuse. It explicitly backdates only synthetic v2 fixture events through SQLite after migration checks, rather than claiming that time actually elapsed. It runs local trusted code, calls no model and adds nothing to the AI ledger. Reconstruction, archived replay and the displayed 19-check AI results remain contract 001; full v2 reconstruction integration is a follow-up. See the [2026-10-27 checks and evidence](docs/maintenance/2026-10-27-retention-pilot.md).

## Run the webhook inbox

```sh
export PHOENIX_ADMIN_TOKEN="$(python3 -c 'import secrets; print(secrets.token_urlsafe(32))')"
python3 examples/reference/server.py
```

In another terminal (using the same admin token):

```sh
curl -s http://127.0.0.1:8787/health
curl -s http://127.0.0.1:8787/api/inboxes \
  -H "Authorization: Bearer $PHOENIX_ADMIN_TOKEN" \
  -H 'Content-Type: application/json' -d '{"name":"My local inbox"}'
# Keep the returned id and token. Tokens are shown only at creation.
curl -s "http://127.0.0.1:8787/hooks/INBOX_ID/INBOX_TOKEN" \
  -H 'X-Webhook-Id: example-001' -d '{"event":"hello.phoenix"}'
```

This is a local development service. It does not currently deliver outbound webhooks, host production traffic, or implement tenant-level authorization.

## Check the notebook in a browser

Optional development checks use Python 3.10+ and pinned Playwright with headless Chromium. They exercise the real notebook under a Pages-style project path: ledger metrics, filters, language persistence, every evidence dialog and artifact link, contract navigation, and loading-error recovery. These checks run in CI and before Pages publication; they do not change the service's Python 3.9+ requirement or add runtime dependencies.

See [browser test setup](docs/setup.md#notebook-browser-checks) and the [2026-10-22 checks and reconstruction record](docs/maintenance/2026-10-22-notebook-browser-checks.md).

## Help change the contract

Propose an observable requirement with a concrete acceptance example using the [contract-change issue template](https://github.com/KeyOfTheQuestion/phoenix-rebuild/issues/new?template=contract-change.yml). Good next challenges: expiry policies, schema migration, and bounded replay with an explicit destination policy. Published results must retain failures and disclose human intervention. See [CONTRIBUTING.md](CONTRIBUTING.md) and the [roadmap](docs/roadmap.md).

Built in public by [KeyOfTheQuestion](https://github.com/KeyOfTheQuestion). Inspired by specification-driven software engineering, including [StrongDM Attractor](https://github.com/strongdm/attractor). Phoenix's focus is a small, inspectable reconstruction experiment with executable data compatibility evidence.

<details><summary>Preview the experiment notebook</summary>

[![Phoenix notebook launch-day screenshot](docs/preview.png)](https://keyofthequestion.github.io/phoenix-rebuild/)

</details>

MIT licensed. No tracking, accounts, paid service, or frontend build step for the notebook.
