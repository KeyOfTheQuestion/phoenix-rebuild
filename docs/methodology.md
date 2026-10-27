# Methodology and evidence

## What is measured

The unit of observation is an attempt: one complete generated candidate for a
versioned contract. The current subject is a Python/SQLite webhook inbox. A pass
requires all 19 independent HTTP checks, including concurrent idempotency,
credential handling, restart persistence, and old-data compatibility. Tests
exercise the service as a client; they do not import its implementation.

The reference implementation is written with AI assistance and reviewed during
project development. Its records are `reference-verification`, not independent
AI rebuilds. `ai-rebuild` records come from the executable generator adapter.

## Generation

The adapter creates a temporary directory outside the repository and stages the
public contract and JSON output schema. It starts an authenticated Codex CLI in
read-only mode, with personal configuration excluded by default. The prompt
requests a full Python implementation without tool calls or access to other
files. No reference source, repository history, or verifier is staged there.
This is procedural separation, not a security proof of source blindness. The CLI
and its authentication still run on the user's machine. `--use-user-config`
explicitly includes personal model settings and MCP configuration.

Each failed generation or verification remains a separate ledger entry.
Acceptance failure summaries may be passed to a new attempt; no previous source
is copied. This makes subsequent attempts feedback-assisted, not independent
one-shot samples. Do not interpret the pass ratio as a benchmark score.

`rebuild --container` explicitly selects Docker for acceptance after the host CLI
returns generated source. Runtime/image preparation happens before any model
call. The contract, verifier and predecessor are frozen before generation; only
the contract/schema are staged during generation. The frozen verifier is loaded
inside Docker in this mode, not on the host. Generation still uses the existing
authenticated host CLI and its configured usage allowance.

An acceptance failure can supply check feedback to the bounded next attempt.
A container runtime, timeout or report-validation error instead preserves the
generated source, prompt, provenance and predecessor as `verification-error`,
with zero checks and no acceptance verdict, and stops without another model
call or host fallback. Preflight refusal before generation creates no attempt.
Reports record the requested verification mode separately from actual execution;
generation failure still means `not-executed`. Existing archived reports are not
relabelled. The notebook distinguishes runtime errors and actual execution modes.

## Data continuity

For current runs, the verifier first starts the chosen predecessor and creates
an inbox plus an event using its HTTP API. It then shuts the predecessor down,
starts the candidate against the same SQLite file, and checks the existing event
and its idempotency key. Restart tests stop and restart the candidate itself.
This checks compatibility with the archived predecessor, not arbitrary historical
versions or all possible database states. Early records used a verifier-created
v1 schema fixture; inspect the archived verifier and `compatibility_fixture`.

## Evidence bundle

Each run publishes `server.py`, `contract.md`, `report.json`, and `manifest.json`.
Current records also archive `verifier.py` and, when used, `predecessor.py`.
Actual generation runs include the exact `prompt.md`. The report contains:

- UTC timestamps and check outcomes, including failure messages;
- SHA-256 of the candidate, contract, and verifier;
- predecessor hash and compatibility fixture type, when available;
- generator version, requested model or default-selection policy, and attempt;
- usage returned by the generator (otherwise null);
- monetary cost, always null until a trustworthy billing source is integrated.

SHA-256 detects mismatched artifacts; the maintainer controls both artifacts and
hashes. These are not signed, independently attested credentials. The raw provider
logs stay in ignored local storage because they may include machine or account
context. The public prompt, output and usage are the reproducible record, not a
complete signed transcript. `human_interventions: 0` means the adapter did not
manually edit that generated candidate. It does not mean the contract, harness,
or generation workflow was built without human input.

## Known limits

- Default generation and replay without `--container` execute code in a local
  child process with a minimal environment, retaining the user's operating-system
  permissions and network access.
- Default CLI configuration can change across CLI versions. An unspecified model
  is reported as a selection policy, not assigned a guessed model name.
- HTTP acceptance is intentionally bounded. It cannot establish absence of bugs.
- Tests are public and are not protected against an agent deliberately searching
  for them. The experiment measures cooperative reconstruction, not adversarial
  benchmark integrity.
- Small-sample results do not support statistical claims or model rankings.
- The service is a local development example with one admin, not a production
  multi-tenant product. No outbound replay is implemented in contract 001.
- Automatic improvement is bounded by a task backlog and visible commits.
  A scheduled run can fail or be skipped when the host or authentication is down.

## Notebook verification

The notebook has a separate optional Chromium suite, run in CI and before Pages
publication. It serves the real static files under `/phoenix-rebuild/`, exercises
filters and language persistence, opens every record's dialog, follows report
links, and checks all displayed artifact URLs against the archived manifest.
It also verifies contract navigation and visible failures for unavailable ledger
or report requests. These browser checks do not execute generated services and
do not count as reconstruction attempts or extra backend acceptance checks.

The suite uses fresh browser contexts and local synthetic failure responses.
It covers Chromium desktop behavior, not all browsers, mobile devices or
accessibility requirements. See the [2026-10-22 verification record](maintenance/2026-10-22-notebook-browser-checks.md).

## Optional container verification

`container-verify` runs the unchanged HTTP harness, candidate and predecessor
inside one Docker container. It checks an actual boundary probe, disables
external networking, uses a read-only root and input mount, and limits CPU,
memory, temporary storage and PIDs. Only source snapshots are mounted; host
credentials are not forwarded. The image is pinned by digest. A runtime or
policy failure returns an error without falling back to host execution.

This is a separate verification mode with the current harness. Archived replay
also supports `replay RUN_ID --container`: it validates the full bundle and
supplies the checked candidate, archived verifier and recorded predecessor bytes
without loading archived code on the host. Records without a predecessor keep
the archived verifier's original fixture. Result metadata binds the record,
contract, verifier and source hashes to the container check.

Neither mode adds a reconstruction attempt to the ledger. Daily generation and
replay without `--container` retain local subprocess execution. The shared container/UID
does not protect the evaluator from a deliberately adversarial candidate, and
Docker shares the host kernel. See the [profile and limits](container-runner.md)
and [2026-10-24 archived replay verification record](maintenance/2026-10-24-container-replay.md).

`rebuild --container` does publish a reconstruction bundle, because it calls the
real generator before acceptance. It retains the same container policy and
limitations. `--verification-timeout` bounds container execution separately from
the generator's `--timeout`. The daily reconstruction on the current host
continues to use local execution because Docker is unavailable. Linux CI tests
the adapter with a clearly labelled synthetic CLI fixture returning existing
archived source, plus real Docker acceptance; those fixtures are temporary, make
no model calls and are never added to the public ledger. See the
[2026-10-26 verification record](maintenance/2026-10-26-container-rebuild.md).

## Contract 002 migration/retention pilot

The separate `verify-retention` command checks an AI-assisted development
reference against the second contract's migration and retention behavior. It
uses a v1 predecessor to create an inbox/event through HTTP, compares all v1
logical rows before and after migration, then checks original token and
idempotency behavior. Repeated startup and default-disabled retention must
preserve those rows. Only later does it backdate synthetic v2 fixture events via
SQLite while the service is stopped, explicitly distinguishing fixture aging
from elapsed real-world time and from migration evidence.

Thirteen focused checks cover policy validation/persistence, explicit bounded
expiry, concurrent batch accounting, preservation of recent and other-inbox
events, and idempotency-key reuse after deletion. The pilot runs local trusted
code and makes no model call. Its reports are separate maintenance evidence,
with source/contract/verifier/shared-client hashes; they do not enter the AI
ledger, count as AI successes, or replace the frozen 19-check contract-001
verifier. Full v2 reconstruction acceptance and archive integration remain
follow-ups. See the [2026-10-27 pilot record](maintenance/2026-10-27-retention-pilot.md).

## Reproduce, don't infer

Run `python3 -m phoenix replay RUN_ID` for trusted records with an archived verifier.
Replay validates the requested ledger entry, manifest, report and artifact hashes
before loading any archived code. It rejects unlisted artifacts, symlinked files,
unsafe record IDs and generation failures with exit code 2. In local mode it compiles the
verified verifier bytes directly and writes the checked candidate and predecessor
bytes into a temporary directory; it neither reopens the original source files
after validation nor writes bytecode into the archive. This guards consistency,
not the trustworthiness of code whose manifest was also deliberately replaced.
The [2026-10-21 maintenance record](maintenance/2026-10-21-replay-integrity.md)
documents the original bypass, regression cases and verification.

With `--container`, replay sends the same verified source bytes to the Docker
runner and executes the archived verifier only inside the restricted container.
`--timeout` bounds container execution (90 seconds by default); image preparation
has separate deadlines. Missing Docker returns exit code 2 with no host fallback.

For an earlier record, run `python3 -m phoenix verify --candidate
docs/evidence/RUN_ID/server.py` with the current harness and disclose the change.
Run `python3 -m phoenix audit` to validate the published ledger and artifact
consistency. Never manually change a failure into a pass.
