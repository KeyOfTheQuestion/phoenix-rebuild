# Changelog

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
