# 2026-11-05: Docker acceptance during contract-002 generation

Roadmap item 11 is implemented: `rebuild --contract 002 --container` now routes
the full frozen `retention-ai-v1` package through the existing Docker runner.
Contract 001 and local acceptance remain the defaults.

```sh
python3 -m phoenix rebuild --contract 002 --container --verification-timeout 90 \
  --predecessor docs/evidence/20261103T023452Z-4ec8a9/server.py
```

## Routing and failures

Docker/image preparation happens before the CLI version check or generation.
The adapter captures the contract, AI entrypoint, four helper modules and chosen
v1 predecessor before provider use. The fresh generation directory still
contains only the contract and output schema. After generation, the runner
receives those frozen bytes and the exact captured candidate, with the explicit
AI profile. Archived verifier modules load only inside Docker in this mode.

The existing runner checks all eight source/contract hashes, contract/profile
and AI kind, complete 32-check passing results, measured runtime and the actual
boundary probe. The pinned image, resource limits, read-only mounts, absence of
host credentials and loopback-only network policy are unchanged. This remains
a cooperative evaluator sharing a container/kernel with its subject, not a
hostile-code isolation guarantee.

A later runtime, timeout or invalid-report error preserves the candidate,
predecessor, prompt and full verifier dependency set as `verification-error` /
`docker-container-attempted`, with zero checks and no acceptance verdict. It
stops without host fallback or another model call. Generation failures remain
`not-executed` and retain the requested Docker mode separately; actual failed
acceptance can still provide check summaries to the bounded second attempt.

## Verification and controls

- Python 3.9.6: **98 unit tests passed**, 50.039 seconds.
- Python 3.14.6: **98 unit tests passed**, 17.487 seconds. Existing SQLite
  resource warnings are not represented as warning-free behavior.
- Five container-focused controls cover preflight refusal before CLI use,
  frozen helper/contract/predecessor bytes despite mutations during generation,
  no host evaluator, runtime-error preservation without retry, retained failed
  acceptance plus feedback, and generation timeout without starting acceptance.
  Before implementation these five tests produced six failing subcases against
  the old v2 refusal; all passed afterward. Fixtures are synthetic and temporary.
- Actual local unavailable-Docker preflight returned exit 2 with an unchanged
  public ledger. Docker acceptance did not run and no model attempt was created.
- Existing CI/Pages now run the synthetic CLI adapter with **Docker acceptance
  during generation**, audit its disposable complete archive, then independently
  replay it in Docker. Both reports must pass 32/32 and retain all bindings.
  A guard refuses host archived-verifier execution during generation. The JSON
  marker remains `CONTAINER_EVIDENCE=`, with `generation_verification` and fresh
  `verification` results under `contract_002_generator_control`. These controls
  call no model and never enter the public ledger.

The current host still has no Docker or optional Playwright installation;
actual container/browser outcomes come from the existing remote workflows.
No local Docker result is claimed.

Independent reference HTTP acceptance passed **19/19**, and the public evidence
audit passed. The optional local synthetic-CLI adapter control also passed
32/32 in a disposable archive without a model call. The current runtime-matrix
smoke checks v1 AI, v2 reference and the latest v2 AI archives separately.

## Today's single real generation cycle

```sh
python3.9 -m phoenix rebuild --contract 002 --attempts 2 --timeout 300 \
  --predecessor docs/evidence/20261103T023452Z-4ec8a9/server.py
```

This host used its already-installed system **CPython 3.9.6 / SQLite 3.43.2**.
The command above illustrates selecting a Python 3.9 executable. The existing
authenticated CLI was **codex-cli 0.166.0**, compared with 0.145.0 in the previous
daily record; this run did not install or update it. It used clean defaults
(`--ignore-user-config`), no model override and the existing authentication.
Resolved model identity remains unknown; a CLI version change does not establish
that the default model stayed the same or identify which model was used.

| Actual observation | Value |
| --- | --- |
| AI record | [`20261105T023912Z-690f7b`](../evidence/20261105T023912Z-690f7b/report.json) |
| Execution | Local subprocess; no Docker claim |
| Acceptance began (UTC) | 2026-11-05 02:39:10.859432 |
| Attempt and result | First attempt, 32/32 passed; no second model call |
| Baseline / retention | 19/19 and 13/13 passed |
| Generation duration | 88.884 seconds |
| Observed input / output / reasoning tokens | 22872 / 3677 / 78 |
| Cached input / cache-write input tokens | 0 / 0 |
| Observed tool calls / manual candidate edits | 0 / 0 |
| Resolved model identity / monetary cost | Unknown / unknown |

The latest eligible successful v1 AI predecessor was
`20261103T023452Z-4ec8a9`. The newer November 4 AI program is v2 and cannot be
used as the current v1 migration fixture. Both acceptance phases created their
own old data through predecessor HTTP; synthetic expiry timestamps were aged
only after migration checks. No real-world elapsed-time claim is made.

The [manifest](../evidence/20261105T023912Z-690f7b/manifest.json) binds the exact
prompt, candidate and complete frozen dependencies. Candidate SHA-256 is
`dfa7d4030b30f2a33bacbc172459ddc265aeb25d70f92b2aeb5bab0a996a6be9`;
predecessor SHA-256 is
`e2b2f0485fd8ebccee134abe2aea93c35046bfb5e25d75ac287ad16442da22d3`.
The frozen AI entrypoint remains
`f6429958cf2f3f9d00ddb8601f5c86a4ba0767046b4fc15361366fc3165596c5`.
Today's audit helper snapshot is
`3f9a5d8c7b200c16d482d114a16fb8c3cd2bf5c455f625ee839faba946b1b7c0`,
including the verifier-binding hardening disclosed after yesterday's generation;
yesterday's earlier helper snapshot stays unchanged in its archive.

The [fresh frozen-archive matrix](2026-11-05-ai-v2-matrix.json) repeats the same
candidate and verifier package on the installed Python runtimes. It creates no
new AI attempt and never changes the original local report or archived source.
Both **CPython 3.9.6 / SQLite 3.43.2** and **CPython 3.14.6 / SQLite 3.53.3**
passed **32/32**. These two observations do not establish compatibility with all
Python/SQLite versions.

## Method changes and limits

Contract-002 text now documents optional Docker acceptance during generation.
Its endpoint/schema requirements and version 2.0.0 are unchanged; the exact
text hash changes to
`8585891ec40f537a09ee306442845532c19d22122182b21f0f6041b2288bb374`.
Shared baseline/retention check source, the AI entrypoint and both reference
implementations are unchanged today. Historical bundles and outcomes are
preserved, including the November 4 reference to local-only v2 generation.

The current migration fixture still needs a successful **v1 predecessor**.
Using the newest v2 AI source as that predecessor would not establish v2-to-v2
policy/data continuity; roadmap item 10 remains separate. Daily generation on
this host stays explicitly local. No model/account permission change, new
credential, credit purchase or third-party promotion is involved.
