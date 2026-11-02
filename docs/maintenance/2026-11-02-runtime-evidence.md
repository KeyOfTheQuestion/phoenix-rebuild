# 2026-11-02: measured Python and SQLite execution runtime

New acceptance and replay results identify the actual Python implementation,
Python version and loaded SQLite library version in `execution_runtime`.
This addresses the runtime-observation part of roadmap item 9 after the
[October 30 cross-runtime failure](2026-10-30-retention-container.md#cross-runtime-failure-discovered-by-ci).
The earlier candidate, its original pass and its Python 3.9 failure remain
unchanged. The observations here were made on November 2; no November 1
reconstruction or missing-day success is claimed.

## What the metadata means

```json
{
  "execution_runtime": {
    "python_implementation": "CPython",
    "python_version": "3.9.6",
    "sqlite_version": "3.43.2"
  }
}
```

These are the actual versions measured during today's floor check, not model
identities, executable paths, inferred versions or a compatibility range.
The evaluator starts both subject and predecessor with its own `sys.executable`.
Reports publish only these three version strings; machine/account names,
interpreter paths, environment dumps and credentials remain absent.

Current baseline, focused/composed retention checks and new public summaries
retain this observation. The audit validates the exact keys and nonempty bounded
version strings when present. Legacy missing/null fields remain unknown rather
than being filled with the current machine's versions. Evidence dialogs show
the recorded runtime in English/Chinese or explicitly say it was not recorded.

Replay measures its own runtime. The local CLI adds this current observation
after the verified frozen evaluator returns, including for older evaluators
without metadata support. The Docker entrypoint measures versions inside the
actual container after evaluation, and host validation requires a complete
observation. It never reports the client's Python version as the container's.
Original reports, source snapshots, verdicts and manifests are not rewritten.
The metadata is a maintainer-controlled observation, not independent attestation
or evidence that an adversarial candidate cannot alter its process.

The verifier source changed from
`ae420dcbc20b8599253a9af43dd31c6162a2acc2ca240a046e03148186482f8e`
to `494d66e9c3e66b33596ebb0ba55e68d3db32347bc1e658c3bc1f0a87f3ebe0b1`.
The baseline's same 19 HTTP checks and acceptance rules, the composed 32 IDs,
both contracts and reference service implementations are unchanged. Older frozen
verifiers remain untouched. The current container entrypoint also changes to
collect runtime metadata; its image, permissions and resource policy do not.

## One real reconstruction on the Python floor

The installed system Python 3.9.6 ran the whole command:

```sh
/usr/bin/python3 -m phoenix rebuild --attempts 2 --timeout 300 \
  --predecessor docs/evidence/20261030T024939Z-60a62e/server.py
```

Attempt 1 passed 19/19, so no second attempt was made. The latest successful
AI candidate created the legacy inbox/event through real HTTP before the new
candidate opened the same SQLite database. The generation adapter still used
the existing authenticated Codex CLI and its clean default configuration.
Only the contract and response schema were staged; no reference or verifier
was supplied to the generator. No model/account override, new credentials,
credit purchase or manual candidate edit was made.

- Actual record: [`20261102T020351Z-0ba772`](../evidence/20261102T020351Z-0ba772/report.json).
- Acceptance began at `2026-11-02T02:03:50.413672+00:00` (UTC).
- Candidate SHA-256: `9302b4710f7381addb3905ac7d9ba8806e27a6a294cf902137bce4b48e42e3f3`.
- Predecessor SHA-256: `ef206e5d9870e14533307b799330a97c12278953919b7e22470704e4c11e6ad6`.
- Observed runtime: CPython 3.9.6 / SQLite 3.43.2, local subprocess.
- CLI: `codex-cli 0.145.0`; clean default; resolved model identity unknown.
- Generation took 60.834 seconds; zero observed tool calls.
- Observed usage: 17,829 input, 4,674 output and 951 reasoning output tokens;
  zero cached/cache-write input tokens. Monetary cost remains unknown.

The same unedited candidate and frozen verifier were then replayed without
another model call:

| Observation | Actual result |
| --- | --- |
| [Replay on CPython 3.9.6 / SQLite 3.43.2](2026-11-02-ai-replay-python39.json) | 19/19 passed |
| [Replay on CPython 3.14.6 / SQLite 3.53.3](2026-11-02-ai-replay-python314.json) | 19/19 passed |
| [Reference HTTP acceptance on Python 3.9.6](2026-11-02-reference-v1.json) | 19/19 passed |
| [Composed retention reference on Python 3.9.6](2026-11-02-retention-full.json) | 32/32 passed |
| [Older frozen retention archive replay](2026-11-02-legacy-retention-replay.json) | 32/32 passed; archive unchanged; fresh runtime recorded |

These are bounded observations on these exact installed versions, not proof
of compatibility with every Python/SQLite version or production safety.
Reference verification and replay do not create AI attempts. Daily instructions
now prefer an already-installed Python 3.9 and require disclosure if it is absent;
Phoenix does not silently install or select another interpreter. A repeatable
candidate compatibility matrix remains a follow-up.

## Verification controls

The full dependency-free suite passed 77/77 on Python 3.9.6 (32.315 seconds)
and 77/77 on Python 3.14.6 (10.116 seconds). Evidence audit, Python compilation
and notebook JavaScript syntax checks passed before commit.

Five new runtime unit controls compare independent child-process measurements
with HTTP/composed reports, check metadata publication and malformed observations,
retain unknown legacy reports and verify current-runtime legacy replay without
archive changes. A new container control refuses missing/incomplete runtime
observations; Docker test fixtures carry explicitly synthetic version metadata.
Existing archive and check-set controls remain active. All fixture ledgers are
temporary; none becomes a public AI result.

The browser suite adds recorded/missing runtime cases in both languages using
synthetic responses. Existing Linux CI and Pages workflows run the full unit,
HTTP, container and browser checks. The actual container smoke now checks the
in-container CPython 3.12 version and SQLite observation for current and archived
replay; legacy retention smoke independently checks its subprocess's versions.
This host has no Docker or optional Playwright installation, so no local Docker
or Chromium run is claimed. Actual remote results are available in the existing
workflow logs; `CONTAINER_EVIDENCE=` includes the measured versions.

The Python 3.14 unit run emitted SQLite `ResourceWarning` messages for unclosed
connections in existing evaluator/fixture paths. They are retained in ignored
local logs; a passing suite is not a claim of warning-free database cleanup.
This runtime-metadata change does not repair those separate cleanup issues or
modify historical evaluator source.
