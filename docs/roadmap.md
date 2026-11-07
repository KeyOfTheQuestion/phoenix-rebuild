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

1. [Implemented 2026-10-23; archive integration 2026-10-24; rebuild integration 2026-10-26] Optional disposable Docker acceptance runner with resource limits, no host credentials forwarded and an actual boundary probe ([profile](container-runner.md), [initial verification](maintenance/2026-10-23-container-runner.md)). `replay --container` uses verified archive bytes. `rebuild --container` now verifies generated candidates with frozen inputs, preflights the runtime before model use, and retains runtime failures without host fallback or another generation attempt ([verification record](maintenance/2026-10-26-container-rebuild.md)). Generation itself still uses the host CLI; local verification remains the default.
2. [Done in initial release] Freeze the exact verifier source at the start of generation, even if the harness changes while a provider runs.
3. Capture the actual resolved model identity when the CLI exposes a reliable field.
4. [Done 2026-10-22] Add an end-to-end browser check for ledger filters, language and artifact links, including a Pages publication check ([verification record](maintenance/2026-10-22-notebook-browser-checks.md)).
5. [Pilot 2026-10-27; composed acceptance 2026-10-28; reference archive 2026-10-29; container replay 2026-10-30; AI generation 2026-11-04] Contract 002 now has atomic migration, bounded expiry and full frozen 32-check AI reconstruction via `rebuild --contract 002`. AI archives use `retention-ai-v1`; reference rows retain their separate profile and stay outside AI metrics. Both support local, Docker and runtime-matrix replay. Default generation remains contract 001 with local acceptance; v2 generation also supports explicit Docker acceptance (2026-11-05). The first actual v2 attempt passed 32/32 on Python 3.9 and replayed on 3.9/3.14 ([evidence and limits](maintenance/2026-11-04-retention-ai.md)).
6. [Done in initial release] Exercise continuity between two different successful AI rebuilds (use `rebuild --predecessor docs/evidence/RUN/server.py`).
7. Add another provider adapter with the same evidence format.
8. [Loopback pilot 2026-11-07] `deliver-event` now reads one stored event and
   posts its original bytes to an explicitly selected IPv4 loopback receiver,
   with a worker-wide deadline, response cap, selected headers and truthful
   uncertain outcomes ([method and checks](maintenance/2026-11-07-loopback-delivery.md)).
   Delivery to other hosts, a destination policy and durable outbox remain
   follow-ups before webhook replay.
9. [Runtime observations implemented 2026-11-02; installed-runtime matrix
   implemented 2026-11-03] New acceptance/replay results and evidence dialogs record actual
   Python/SQLite runtime identity ([verification](maintenance/2026-11-02-runtime-evidence.md)).
   Daily instructions prefer an already-installed Python 3.9. `replay-matrix` now
   checks the same verified archive in 1..4 explicitly installed POSIX runtimes,
   retaining all results with deadlines and no fallback ([verification](maintenance/2026-11-03-runtime-matrix.md)).
   An exhaustive compatibility matrix is not claimed. The [October 30 failure](maintenance/2026-10-30-retention-container.md#cross-runtime-failure-discovered-by-ci)
   demonstrates why a 19/19 pass on Python 3.14 is insufficient to establish
   Python 3.9 compatibility. Preserve both runtime observations and candidate bytes.
10. [Implemented 2026-11-06] Add `verify-continuity` and `replay-continuity` for a
    distinct eight-check v2-to-v2 fixture covering existing/disabled policies,
    events, idempotency, restart and bounded expiry. Optional complete frozen
    bundles remain outside the AI ledger and replay from validated temporary
    copies. This fixture is local only; the existing 32-check reconstruction
    still starts from a v1 predecessor ([method and evidence](maintenance/2026-11-06-v2-continuity.md)).
11. [Done 2026-11-05] Extend the existing preflight/failure-preservation behavior
    to Docker acceptance during contract-002 generation. The adapter supplies
    the full frozen `retention-ai-v1` package, preserves failures without host
    fallback and supports independent archive replay. Existing CI/Pages exercise
    real Docker with temporary synthetic CLI generation ([verification](maintenance/2026-11-05-retention-container-rebuild.md)).

No success streak or daily claim is published before the corresponding evidence
exists. Community requirements must describe observable behavior and acceptance
examples. New safety or compatibility promises need executable checks.
