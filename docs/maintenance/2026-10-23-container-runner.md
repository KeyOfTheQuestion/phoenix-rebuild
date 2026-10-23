# 2026-10-23: optional Docker acceptance

The roadmap's container execution capability is available as `container-verify`.
It runs the current HTTP harness and candidate in one restricted container, with
an actual boundary probe and separately bounded image preparation/execution.
See the [profile, setup and limits](../container-runner.md).

This adds a separate verification mode. Daily generation and historical replay
retain local execution; historical records have not been relabeled. The existing
19 HTTP checks and the versioned backend contract are unchanged. Docker still
shares the host kernel and the candidate shares a container/UID with the verifier;
this is a cooperative experiment, not hostile-code certification.

## Local checks and real container checks

The development host has no Docker or Podman executable. The real command
`python3 -m phoenix container-verify` exited 2 with:

```text
Docker is not installed; container verification did not run
```

The eight runner unit tests use a fake Docker client. They check source-only
staging, snapshot binding, policy options, environment non-forwarding, invalid
reports, missing runtime refusal, bounded timeouts and requested cleanup. Their
results are not described as real container execution.

The [CI workflow](https://github.com/KeyOfTheQuestion/phoenix-rebuild/actions/workflows/ci.yml)
contains a separate Linux `container` job that runs `tests/container_smoke.py`;
the existing Pages workflow runs the same command before publication. It emits
an actual `CONTAINER_EVIDENCE=` JSON record with:

- reference HTTP acceptance inside Docker;
- the latest audited AI candidate and its recorded predecessor inside Docker;
- observed UID, loopback-only interfaces, read-only mounts, tmpfs restrictions,
  absent Docker socket/host marker, and actual cgroup-v2 resource limits;
- a deliberately slow subject refused at its two-second execution deadline,
  followed by a check for leftover Phoenix containers.

The CI output is the source for actual container runtime results. No model calls
are made in those checks. The local reconstruction below remains a separate,
published AI attempt.

## Reproduce the container check

On a Linux cgroup-v2 Docker host:

```sh
python3 -m phoenix container-verify
python3 tests/container_smoke.py
```

- Pinned image: `python:3.12-slim-bookworm@sha256:6a0e06f5ba0b373d051aabd3fffb9b873f27f9a2a3a14bcbe37dee6f0e3bbc70`.
- Runner source SHA-256: `67e9824c3e5592df260d829fcc55b6aae41cb7752659b7b5b5d1b12b6e117d4a`.
- Entrypoint SHA-256: `9745924e4b8c3b55b26a14dd6f1d6b275d50ba4324192a5ec49a77f4519937f7`.
- Current verifier SHA-256: `435142a64f7d62b54d58c5187973e237ca2140d25833b464d3d68279c0fdab6d`.

First-use image inspection/pull has its own bounded deadlines. Container
execution defaults to 90 seconds; timeout cleanup requests forced removal of
only the invocation's unique container. Unsupported runtime policies and
malformed results fail explicitly without host fallback. Runtime diagnostics
stay in ignored local storage; the CI fixtures use only public synthetic input.

## Local verification output

```text
python3 -m unittest discover -s tests -v
test_cli_reports_missing_runtime_with_exit_two (test_container_runner.ContainerRunnerTests) ... ok
test_execution_timeout_is_bounded_before_contacting_docker (test_container_runner.ContainerRunnerTests) ... ok
test_failed_process_or_invalid_json_cannot_return_a_verdict (test_container_runner.ContainerRunnerTests) ... ok
test_inconsistent_hashes_counts_or_incomplete_passing_results_are_refused (test_container_runner.ContainerRunnerTests) ... ok
test_missing_docker_refuses_execution_without_a_host_fallback (test_container_runner.ContainerRunnerTests) ... ok
test_only_snapshots_are_mounted_and_the_host_environment_is_not_forwarded (test_container_runner.ContainerRunnerTests) ... ok
test_timeout_requests_removal_of_the_entire_container (test_container_runner.ContainerRunnerTests) ... ok
test_unconfirmed_boundaries_are_refused (test_container_runner.ContainerRunnerTests) ... ok
test_an_always_200_service_cannot_claim_success (test_experiment.AcceptanceHarnessTests) ... ok
test_recreating_database_at_boot_fails_data_continuity (test_experiment.AcceptanceHarnessTests) ... ok
test_reference_meets_contract_with_a_real_predecessor_database (test_experiment.AcceptanceHarnessTests) ... ok
test_ledger_cannot_change_a_verdict_without_matching_report (test_experiment.PublishedEvidenceTests) ... ok
test_modified_candidate_is_detected (test_experiment.PublishedEvidenceTests) ... ok
test_published_ledger_is_internally_consistent (test_experiment.PublishedEvidenceTests) ... ok
test_extra_unlisted_file_is_refused (test_replay_integrity.ReplayIntegrityTests) ... ok
test_generation_failure_is_not_treated_as_executable_source (test_replay_integrity.ReplayIntegrityTests) ... ok
test_malformed_ledger_produces_a_controlled_error (test_replay_integrity.ReplayIntegrityTests) ... ok
test_manifest_cannot_omit_a_verifier_that_replay_would_load (test_replay_integrity.ReplayIntegrityTests) ... ok
test_modified_candidate_is_refused_before_startup (test_replay_integrity.ReplayIntegrityTests) ... ok
test_modified_verifier_is_refused_before_execution (test_replay_integrity.ReplayIntegrityTests) ... ok
test_replay_uses_verified_snapshot_if_disk_files_change_after_inspection (test_replay_integrity.ReplayIntegrityTests) ... ok
test_symlinked_artifact_is_refused_even_when_bytes_match (test_replay_integrity.ReplayIntegrityTests) ... ok
test_unsafe_and_unlisted_run_ids_are_refused (test_replay_integrity.ReplayIntegrityTests) ... ok
test_valid_archive_replays_without_writing_into_evidence (test_replay_integrity.ReplayIntegrityTests) ... ok

----------------------------------------------------------------------
Ran 24 tests in 2.661s

OK

python3 -m phoenix audit
Evidence audit: passed
```

Additional checks completed successfully:

| Check | Result |
| --- | --- |
| Independent reference HTTP acceptance | 19/19 passed |
| Archived replay of today's AI candidate | 19/19 passed |
| Notebook Chromium suite | 7/7 passed |
| Maintained Python source compilation | Exit 0 |
| Embedded container entrypoint compilation | Exit 0 |
| Notebook JavaScript syntax | Exit 0 |

## Today's bounded reconstruction

One cycle allowed at most two attempts and 300 seconds per attempt. Attempt 1
passed 19/19 checks, so no second attempt was run. It used the existing
CLI authorization and clean default configuration without a model override.

- Record: [`20261023T023013Z-e63562`](../evidence/20261023T023013Z-e63562/report.json).
- Verification started at `2026-10-23T02:30:12.736032+00:00` (UTC).
- Candidate SHA-256: `35c8091e910d57db44858879aedd4d5316f43909f4b96140c799131688cbbaba`.
- Predecessor: `20261022T014405Z-195739`, the latest successful AI candidate before this run.
- Predecessor SHA-256: `71aeb29b9af0ceff0876881e437da1fe7a1ae000d21d2594f546d1cce8379ff3`.
- Continuity: an inbox/event created through the predecessor's HTTP API was read
  by the new candidate from the same SQLite file, preserving its idempotency key.
- Generator: codex-cli 0.154.0, 84.566 seconds, zero observed tool calls.
- Execution for this reconstruction record: local subprocess, as recorded.
- Model identity and monetary cost remain unknown; usage comes only from the
  actual CLI response in the published report.

The previous generation failure stays visible in the ledger. No new account
credentials or permissions were created to complete this iteration.
