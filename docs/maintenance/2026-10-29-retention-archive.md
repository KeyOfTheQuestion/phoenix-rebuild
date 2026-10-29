# 2026-10-29: frozen contract-002 reference archives

The 32-check retention reference can now publish and replay a complete frozen
source package. This closes the reference-archive part of the second-contract
roadmap item; v2 AI generation remains a follow-up.

```sh
python3 -m phoenix verify-retention --full --publish \
  --predecessor docs/evidence/20261029T023202Z-360ada/server.py
python3 -m phoenix replay 20261029T023917Z-bce8be
```

The [published reference bundle](../evidence/20261029T023917Z-bce8be/report.json)
passed 32/32. It migrated data created through today's v1 AI candidate's real
HTTP API. The separate [actual frozen replay](2026-10-29-retention-replay.json)
also passed 32/32 and left every archive byte and directory entry unchanged.
Neither reference verification nor replay called a model.

## Frozen dependency closure

Publication captures all inputs before evaluation, evaluates captured temporary
copies and publishes those same bytes. The manifest binds nine artifacts:

| Artifact | Purpose |
| --- | --- |
| `server.py` | AI-assisted contract-002 development reference |
| `predecessor.py` | Actual archived v1 AI program used to create migration data |
| `contract.md` | Exact contract 002 input |
| `verifier.py` | Standalone frozen package entrypoint |
| `baseline.py` | The 19 baseline HTTP checks |
| `retention.py` | The 13 focused checks and 32-check composition |
| `records.py` | Frozen project support imported by the verifier |
| `audit.py` | Frozen check-set support imported by the verifier |
| `report.json` | Actual observations, timestamps, source hashes and provenance |

`manifest.json` contains the SHA-256 of each artifact. The report separately
binds the entrypoint, baseline, retention module and both support modules.
The `retention-composed-v1` profile is archive-format version 1 for contract 002,
not a schema-version-1 claim.

Replay validates the ledger, manifest, complete dependency set and report/source
bindings before loading archived code. It stages verified bytes, then creates a
uniquely named private package in another temporary directory and imports the
frozen modules there. It does not import the working checkout's verifier. The
private module names are removed after evaluation. Bytecode, fixture databases
and temporary files stay outside the evidence directory.

This freezes project evaluator sources, not the operating system, installed
Python/SQLite runtime or an independently attested execution environment. The
subject/evaluator still run with normal local process permissions. Hashes detect
inconsistent files; a maintainer controlling files and hashes can replace both.
Only trusted archives should be executed. This is not a hostile-code sandbox.

## Contract and provenance boundaries

New records carry `contract_id`, `contract_version` and `verifier_profile`.
Historical records without these fields retain their original contract-001
interpretation without editing their JSON or source files. The audit requires
the original 19 passing identifiers for contract 001 or the 32 distinct composed
identifiers and full dependency set for contract 002.

Contract 002 currently permits `retention-reference-verification` records only.
It refuses unsupported AI labels, unknown contracts/profiles, incorrect coverage,
missing dependencies and mismatched helper hashes. `--publish` requires `--full`;
the focused 13-check result cannot be published as a 32-check archive. Extra
artifacts must have safe, non-reserved filenames and immutable byte contents.

The notebook labels each row's contract, links every frozen dependency, and
shows contract-002 rows as reference verification with no model call. AI attempt
and success metrics continue to count only `ai-rebuild` rows. Reference checks
are not model successes or extra reconstruction attempts.

Contract-002 archive replay currently supports trusted local execution only.
Requesting `replay RUN_ID --container` returns exit code 2 before archived code
is loaded and never falls back to host execution. Contract-001 container replay
and default generation retain their existing behavior.

## Verification

| Local check | Observed result |
| --- | --- |
| Dependency-free unit suite | 66 passed, 24.749 seconds |
| Contract-001 reference HTTP acceptance | 19/19 passed |
| Focused retention / composed retention | 13/13 and 32/32 passed |
| Published frozen package with today's v1 AI predecessor | 32/32 passed |
| Replay using that published package | 32/32 passed; archive unchanged |
| Evidence audit | Passed |
| Python compilation and notebook JavaScript syntax | Passed |
| Real browser suite | 9 passed, 13.520 seconds |

Nine new archive controls check complete publication, replay despite a failing
live workspace verifier, unchanged archive bytes and private-module cleanup,
helper tampering before execution, report binding after manifest rehashing,
files changing after validation, unsupported container mode with an internally
consistent host-execution marker, invalid contract/AI labels and incomplete
passing results, full-mode publication gating, and safe extra-artifact inputs.
All unit fixture ledgers are temporary; they do not enter the public notebook.

The browser suite additionally checks English/Chinese contract labels, reference
classification, frozen dependency links and exclusion from the AI filter. It
uses Chromium 156.0.8078.4 and Playwright 1.64.0. Existing browser checks still
verify every displayed artifact against its manifest.

The existing CI Python 3.9/3.12/3.13 jobs and Pages publication now replay the
actual frozen reference bundle with a 60-second subprocess deadline, compare
all eight source/contract bindings, check 32/32 and assert archive immutability.
The command is `python3 tests/retention_archive_smoke.py`; its evidence marker is
`RETENTION_ARCHIVE_EVIDENCE=`. No model credentials are used for these checks.
The separate contract-001 Docker regressions remain in the existing workflows.

## Published reference observation

- Record: [`20261029T023917Z-bce8be`](../evidence/20261029T023917Z-bce8be/report.json).
- Acceptance started at `2026-10-29T02:39:15.820763+00:00` (UTC).
- Kind: `retention-reference-verification`; contract `002`, version `2.0.0`.
- Reference SHA-256: `10ecaa50408ecc618224bca8ee26af4840869bc1c3ae1d24ca91cd4bffecbf95`.
- Predecessor: today's actual v1 AI candidate, SHA-256
  `626254c065586ec92e991959156d7b187cfa7038b103360f6a577c9a66105afc`.
- Phase results: baseline 19/19, retention 13/13; combined 32/32.
- Generation usage: null; no model called. Monetary cost remains unknown.

The two phases use the same captured program bytes and separate fixture
databases. Migration data is written through predecessor HTTP. Only later are
synthetic v2 expiry events explicitly backdated through SQLite while stopped;
this is not evidence of a real-world retention interval elapsing.

## Today's one actual reconstruction

The actual model cycle remained contract 001. It used the existing authenticated
Codex CLI with clean default configuration, at most two attempts and a
300-second limit per attempt. Attempt 1 passed 19/19, so no second attempt was
made. Its predecessor was the latest successful AI candidate from October 28.

- Record: [`20261029T023202Z-360ada`](../evidence/20261029T023202Z-360ada/report.json).
- Acceptance started at `2026-10-29T02:32:02.111846+00:00` (UTC).
- Candidate SHA-256: `626254c065586ec92e991959156d7b187cfa7038b103360f6a577c9a66105afc`.
- Predecessor record: `20261028T023142Z-5f1624`, SHA-256
  `7c258e2fcaa928a0f1452b8cb590440bf75bcfdc7ce12d084f03024b186cc822`.
- Verifier SHA-256: `ae420dcbc20b8599253a9af43dd31c6162a2acc2ca240a046e03148186482f8e`.
- CLI: `codex-cli 0.163.0`; clean default; resolved model identity unknown.
- Generation: 52.572 seconds; zero observed tool calls; no manual candidate edit.
- Actual usage: 22,990 input, 3,270 output and 230 reasoning output tokens;
  zero cached/cache-write input tokens. Monetary cost remains unknown.
- Execution: local subprocess, with real predecessor HTTP data continuity.

The new reference archive is a separate observation. Historical failures and
all older evidence remain unchanged. No model override, purchased credits,
account-permission changes or new authentication credentials were used.
