# Changelog

## 2026-11-14 · Delivery history receiver filter

- Added read-only `delivery-history --destination URL`, which combines the stored receiver fingerprint with event, state and cursor filters. [Receiver filter evidence](docs/maintenance/2026-11-14-history-destination.md).

## 2026-11-13 · Delivery history event filter

- Added read-only `delivery-history --event-id ID`. The exact stored event ID can be combined with `--state` and cursor pagination. [Event filter evidence](docs/maintenance/2026-11-13-history-event.md).

## 2026-11-12 · Delivery history state filter

- Added read-only `delivery-history --state unconfirmed|finished`. Filtered pages show pending receipts or saved outcomes in start-time and ID order. [State filter evidence](docs/maintenance/2026-11-12-history-state.md).

## 2026-11-11 · Delivery receipt lookup

- Added `delivery-receipt --attempt-id ID` to read a journal row directly by its primary key. The CLI reports found, missing and invalid lookups with distinct exit codes. [Receipt lookup evidence](docs/maintenance/2026-11-11-receipt-lookup.md).
- Isolated the policy descriptor count check after a CI failure. The check now observes descriptors in a process dedicated to policy validation. [Failure and follow-up](docs/maintenance/2026-11-11-receipt-lookup.md#ci-failure-and-follow-up).

## 2026-11-10 · Read-only delivery history pagination

- Added `delivery-history --cursor TOKEN`, an additive `next_cursor` response and `delivery_history_page`. Continue beyond the first 100 records using start-time/ID keyset ordering; retain the legacy Python list API and SQLite schema.
- Validate bounded canonical cursor encodings before opening the journal. Preserve read-only behavior and receipt uncertainty; document cross-page updates, unsigned seek positions and returned-row versus scan limits.
- Added eight pagination checks, complete traversal of 123 synthetic rows and canonical-guard negative controls. The expanded 38-check client smoke includes an actual AI-backed delivery receipt; see [today's evidence and limits](docs/maintenance/2026-11-10-history-pagination.md).
- Pin the CI verify matrix to Ubuntu 24.04 after `ubuntu-latest` selected 26.04 without a Python 3.9 build. Retain the setup failure and cancelled matrix observations; keep all versions/checks and the existing publication jobs.

## 2026-11-09 · Captured loopback destination policy

- Added optional `deliver-event --destination-policy FILE`: validate a bounded, strict UTF-8 JSON allowlist and require the exact selected loopback URL before HTTP or journal activity. Existing address, redirect, response and deadline restrictions remain.
- Bind captured policy bytes to returned and persisted final receipts with SHA-256. Read once per invocation; policy edits apply next time. Preserve journal format and interrupted-row uncertainty.
- Expanded delivery observations from 21 to 30 checks, including exact-match refusal, complete policy validation, real AI inbox approval and descriptor cleanup after repeated directory/FIFO refusals. See [today's actual evidence and limits](docs/maintenance/2026-11-09-destination-policy.md).

## 2026-11-08 · Persistent local delivery history

- Added optional `deliver-event --journal FILE` and read-only `delivery-history`. Commit an unconfirmed attempt before network activity, then save a redacted final receipt. An interruption never authorizes a resend; final-storage failure preserves the actual HTTP outcome with an explicit flag and exit code 2.
- Publish initialized databases atomically for concurrent first use. Validate raw SQLite identity and rollback mode before opening existing files, preserving unrelated recovery files and refusing WAL without creating sidecars.
- Expanded delivery checks from 12 to 21, including real process interruption, concurrent clients and a locked final commit. Keep historical evidence unchanged; [today's record](docs/maintenance/2026-11-08-delivery-journal.md) separates local journal observations from actual AI reconstruction.

## 2026-11-07 · Bounded loopback delivery pilot

- Added `deliver-event`, reading one stored event and posting its original bytes to an explicit IPv4 loopback HTTP receiver. Enforce source/body/header limits, a worker-wide deadline, a bounded receiver response and one POST attempt.
- Preserve uncertain outcomes after POST starts, validate worker receipts and avoid forwarding source credentials. Add independent HTTP receivers, slow-stream and partial-result regressions, plus actual AI inbox delivery checks in existing CI/Pages.
- Today's reconstruction, continuity checks and local delivery observations are recorded in the [maintenance notebook](docs/maintenance/2026-11-07-loopback-delivery.md). Delivery to other hosts and durable outbox semantics remain follow-ups.

## 2026-11-06 · Separate v2-to-v2 continuity fixture

- Added `verify-continuity --candidate FILE --predecessor FILE [--output-dir DIR]` and `replay-continuity DIR` with eight checks for persistent policies, event data, idempotency, restart, bounded expiry and expired-key reuse across a v2-to-v2 handoff.
- Capture five source/contract inputs before execution. Optional bundles retain those inputs, the report and manifest; replay validates hashes, kind/profile and bindings before executing temporary copies. Failed observations remain evidence.
- Keep these local-only observations outside the AI ledger and the existing 19/32-check rules. The 32-check rebuild still requires a v1 predecessor. Disclose synthetic fixture timestamp aging and normal local process permissions in the [method and evidence record](docs/maintenance/2026-11-06-v2-continuity.md).
- Required regression checks exposed reference connection resets under the eight-client burst. Give both reference entrypoints a listen queue of 32, add a controlled delayed-accept regression, and preserve the original failing report and source identity.

## 2026-11-05 · Docker acceptance during v2 generation

- Added `rebuild --contract 002 --container`: preflight Docker before provider use, then send the complete frozen `retention-ai-v1` package to the existing runner without loading archived verifier code on the host.
- Preserve runtime/report failures as complete zero-check bundles without host fallback or another model call; retain acceptance retries, generation failures and default local/v1 behavior.
- Added four unit tests and replaced the earlier v2 refusal control with preflight coverage. Existing CI/Pages now exercise synthetic CLI generation with real Docker acceptance and independent replay of its disposable archive.
- Today's one actual local v2 cycle passed 32/32 on attempt 1. Disclose the installed CLI change to 0.166.0 and the contract's execution-mode text change in the [verification record](docs/maintenance/2026-11-05-retention-container-rebuild.md). Historical evidence stays unchanged.

## 2026-11-04 · Contract-002 AI reconstruction

- Added `rebuild --contract 002` with the complete frozen 32-check acceptance package, exact prompt, v1 predecessor and four helper dependencies. Default generation remains contract 001; v2 generation currently uses local acceptance and refuses `--container` before provider use.
- Added the distinct `retention-ai-v1` archive/audit profile, including preserved generation/runtime failures, local/Docker replay and existing runtime-matrix support. Reference records retain their own classification and stay outside AI metrics.
- Require the verifier's report digest for both v2 profiles. Preserve the real attempt's pre-hardening audit snapshot and disclose the post-generation repair with negative controls.
- Added eight adapter controls, a synthetic CLI/Docker integration control and browser classification coverage. Preserve historical evidence; disclose the contract metadata change and the v1-only continuity fixture.
- The first real v2 candidate passed 32/32 on its first attempt and replayed successfully on Python 3.9 and 3.14. See the [generation and verification record](docs/maintenance/2026-11-04-retention-ai.md).

## 2026-11-03 · Bounded archive runtime matrix

- Add POSIX `replay-matrix` for 1..4 explicitly installed Python executables. Audit once, execute captured frozen bytes in separate runtimes, validate observations and retain every acceptance/runtime failure without model calls or ledger publication.
- Bound each runtime to 1..300 seconds and request process-group cleanup on timeout. Preserve explicit interpreter symlinks, refuse invalid reports and avoid fallback or automatic runtime installation.
- Keep existing contracts/verifiers and historical evidence unchanged. Extend existing CI/Pages checks to exercise actual v1/v2 matrix replay. Preserve today's failed first AI attempt and feedback-assisted second pass in the [verification record](docs/maintenance/2026-11-03-runtime-matrix.md).

## 2026-11-02 · Measured execution runtime

- Record actual Python implementation/version and SQLite version in new acceptance reports and ledger summaries. Validate metadata shape while preserving unknown legacy observations.
- Measure fresh local replay versions and container versions inside Docker, without rewriting archived reports or substituting host versions for container observations. Evidence dialogs show measured versions or explicitly missing metadata in both languages.
- Keep the 19/32 check sets and service contracts unchanged; disclose the evaluator source change. Run today's single real reconstruction on installed Python 3.9 and preserve its actual evidence in the [verification record](docs/maintenance/2026-11-02-runtime-evidence.md).

## 2026-10-30 · Container replay for frozen retention archives

- Replay complete contract-002 reference archives inside the existing restricted Docker profile, loading archived code only in the container.
- Validate the exact frozen helper set, all eight source/contract hashes, the 32-check profile and the actual boundary probe. Refuse inconsistent inputs/results and unavailable runtimes without host fallback.
- Extend existing Linux CI and Pages container checks to replay a real published v2 reference bundle and assert archive immutability. Default local verification and historical evidence stay unchanged.
- Preserve today's single real contract-001 reconstruction and disclose the changed host/CLI environment in the [verification record](docs/maintenance/2026-10-30-retention-container.md).
- Preserve an additional 17/19 Python 3.9 replay failure for the same unedited AI candidate that passed on Python 3.14. Correct integrity tests to require checked bytes and truthful fresh verdicts, without assuming cross-runtime acceptance or changing historical results.

## 2026-10-29 · Frozen retention reference archives

- Added `verify-retention --full --publish`, capturing the exact entrypoint, four project-module dependencies, candidate, predecessor and contract before execution. Publish the resulting 32-check record as contract-002 reference verification, without a model call.
- Added contract-aware audit profiles and trusted local replay from verified source copies in a private temporary package. Refuse incomplete dependency sets, incorrect bindings, unsupported v2 AI labels and contract-002 container requests without host fallback. Preserve legacy contract-001 bundles unchanged.
- Label contracts in the bilingual notebook, expose frozen dependency links, and keep reference rows outside AI metrics. Add nine archive unit controls, a browser classification check and real frozen-reference replay in existing CI/Pages workflows.
- Preserve today's one actual contract-001 reconstruction separately; see the [source/profile/verification record](docs/maintenance/2026-10-29-retention-archive.md).

## 2026-10-28 · Composed retention acceptance

- Added `verify-retention --full`, composing 19 schema-v2 baseline HTTP checks with the 13 retention/migration checks. Both phases use captured candidate/predecessor bytes and separate fixture databases; all 32 distinct checks must pass.
- Parameterized only the baseline's expected health schema, retaining version 1 and the original 19 checks by default. Expose the expectation in new reports and reject explicitly schema-v2 baseline reports from the contract-001 ledger; historical verifier archives remain unchanged.
- Added nine controls for the 32-check pass, a body-limit regression missed by the focused pilot, captured bytes, missing checks, inconsistent bindings, unchanged v1 checks, input validation, CLI selection and ledger mislabelling. Existing CI/Pages run both retention modes.
- Preserve today's single real contract-001 reconstruction separately from the reference checks; disclose the harness source change and actual negative control in the [verification record](docs/maintenance/2026-10-28-retention-composed.md).

## 2026-10-27 · Retention and migration reference pilot

- Added standalone contract 002 and an AI-assisted reference with atomic additive migration, per-inbox opt-in policies and explicit expiry batches of at most 100 events. Keep v1 rows and column layouts, reject unsupported future versions and preserve recent/other-inbox events.
- Added `verify-retention`, a separate 13-check HTTP pilot covering real v1 predecessor data, policy persistence, deterministic disclosed fixture aging, concurrent expiry and idempotency-key reuse. It makes no model call and does not publish AI attempts or change the contract-001 reconstruction verifier.
- Added nine focused unit controls, including false-green/destructive/no-op/over-eager negative candidates, mid-migration rollback, future-version refusal and reordered-column compatibility. The existing CI and Pages workflows now require the pilot.
- Published today's bounded contract-001 AI reconstruction and separate reference verification in the [maintenance record](docs/maintenance/2026-10-27-retention-pilot.md). Full v2 reconstruction integration remains a follow-up.

## 2026-10-26 · Opt-in Docker acceptance during rebuild

- Added `rebuild --container --verification-timeout 90`: prepare Docker/image before model use, then verify generated source with frozen verifier/predecessor bytes inside the existing restricted container. Generation remains on the host CLI; local acceptance is still the default.
- Preserve generated source and provenance as `verification-error` when container execution cannot provide a validated report. Stop without host fallback or another model call, and distinguish this from acceptance failure in the audit, CLI exit code and bilingual notebook.
- Added eight adapter unit controls and one browser test covering execution labels/runtime errors. Extended existing Linux CI with a synthetic CLI fixture and real Docker acceptance; temporary fixture records never enter the public ledger or count as new AI rebuilds.
- Record the single actual daily reconstruction, unchanged contract/harness and local verification in the [verification record](docs/maintenance/2026-10-26-container-rebuild.md).

## 2026-10-24 · Archived verifier replay inside Docker

- Added `replay RUN_ID --container --timeout 90`, using the exact verified archive bytes for the candidate, archived verifier and recorded predecessor. Archived code is not loaded on the host in this mode; unavailable Docker never triggers host fallback.
- Preserve early SQL-fixture records without substituting a current verifier or reference predecessor. Output identifies the record, contract and source hashes without changing the historical ledger.
- Added seven focused unit cases for byte snapshots, archive gating, host-execution refusal, post-validation disk changes and original fixture selection. Extend real Linux CI to replay both the latest AI record and an early record with a distinct archived verifier.
- Record today's actual bounded reconstruction and tool version in the [verification record](docs/maintenance/2026-10-24-container-replay.md). Daily generation remains local; model identity and cost are still unknown.

## 2026-10-23 · Optional Docker verification

- Added `container-verify`, a separately invoked HTTP acceptance mode with a digest-pinned Python image, only read-only source snapshots mounted, external networking disabled, and CPU/memory/PID/tmpfs limits.
- Validate actual container boundary observations, snapshot hashes and complete passing verdicts; refuse unavailable runtimes and unconfirmed policies without a host fallback.
- Added eight dependency-free runner unit tests and explicit Linux Docker integration checks for the reference, latest audited AI candidate, real predecessor data and timeout cleanup. CI and the existing Pages workflow run the integration command.
- Default generation and archived replay remain local. The [verification record](docs/maintenance/2026-10-23-container-runner.md) distinguishes local checks, real CI container checks and today's bounded reconstruction.

## 2026-10-22 · Notebook browser verification

- Added seven optional Playwright/Chromium checks for notebook metrics, record filters, persisted language selection, all evidence dialogs and artifact links, contract navigation, and failed ledger/report loading.
- Exercise the real static site under a Pages-style project subpath and verify displayed artifact bytes against their archived hashes.
- Run the browser suite in CI and before publication through the existing Pages workflow. Keep the service and backend harness dependency-free on Python 3.9+; optional browser tools require Python 3.10+.
- Documented actual browser results, a deliberately broken filter negative control and the bounded daily reconstruction in the [verification record](docs/maintenance/2026-10-22-notebook-browser-checks.md).

## 2026-10-21 · Replay integrity

- Fixed archived replay loading source before checking its manifest and hashes.
- Added shared bundle validation, controlled refusal of malformed records and generation failures, and execution of verified bytes in temporary copies without modifying archives.
- Added ten regression cases covering modified source, missing/unlisted artifacts, symlinks, unsafe IDs, malformed ledgers, generation failures, valid replay and source changes after validation.
- Documented the reproduction, original code location, repair and verification in the [maintenance record](docs/maintenance/2026-10-21-replay-integrity.md). Historical evidence remains unchanged.

## 0.1.0 · 2026-10-20

- Added contract 001, a standard-library webhook inbox, and 19 independent HTTP checks.
- Added actual structured Codex CLI generation, bounded retries, and an explicit failure ledger.
- Added true predecessor-to-candidate database continuity through HTTP-created data.
- Added frozen contract/verifier/predecessor inputs at the start of generation, so concurrent harness development cannot mislabel a run's verification source.
- Added archived-verifier replay, manifest integrity auditing, and a bilingual public notebook.
- Added negative harness checks for false-green servers, destructive initialization and modified evidence.

Initial development records include a rejected personal CLI configuration and
an early AI success evaluated against a verifier-created schema fixture. Before
the first public release, the initial missing placeholder hash and the early
run's verifier snapshot attribution were corrected without changing its candidate
or test outcomes; the report carries an explicit metadata note. Later runs use
the frozen verifier and actual predecessor-created data. No failed attempt was
removed or relabeled as a success.
