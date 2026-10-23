# Changelog

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
