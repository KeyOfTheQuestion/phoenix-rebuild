# Roadmap

## v0.1 · Inspectable reconstruction

- [x] Useful standard-library webhook inbox and versioned contract.
- [x] Independent HTTP acceptance, concurrency, restart and legacy-data checks.
- [x] Real Codex CLI generator with bounded attempts and fresh directories.
- [x] Failed attempts, candidate source, contract, hashes and reported usage.
- [x] Static bilingual notebook with inspectable evidence.
- [x] Re-run an archived candidate using its archived verifier.
- [x] Check the complete evidence bundle before replaying archived code, and execute the verified bytes without changing the archive (2026-10-21; [repair and checks](maintenance/2026-10-21-replay-integrity.md)).

## Next bounded improvements (one meaningful change per iteration)

1. Run candidates in a disposable container with resource limits and no credentials.
2. [Done in initial release] Freeze the exact verifier source at the start of generation, even if the harness changes while a provider runs.
3. Capture the actual resolved model identity when the CLI exposes a reliable field.
4. [Done 2026-10-22] Add an end-to-end browser check for ledger filters, language and artifact links, including a Pages publication check ([verification record](maintenance/2026-10-22-notebook-browser-checks.md)).
5. Add a second contract: retention/expiry with explicit migration from v1 data.
6. [Done in initial release] Exercise continuity between two different successful AI rebuilds (use `rebuild --predecessor docs/evidence/RUN/server.py`).
7. Add another provider adapter with the same evidence format.
8. Add reproducible, bounded outbound delivery before considering webhook replay.

No success streak or daily claim is published before the corresponding evidence
exists. Community requirements must describe observable behavior and acceptance
examples. New safety or compatibility promises need executable checks.
