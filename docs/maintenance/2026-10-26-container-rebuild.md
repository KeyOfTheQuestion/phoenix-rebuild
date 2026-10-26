# 2026-10-26: opt into Docker acceptance during a rebuild

The container-runner roadmap item now reaches the actual generator adapter:

```sh
python3 -m phoenix rebuild --container --verification-timeout 90 \
  --attempts 2 --timeout 300 --predecessor docs/evidence/RUN_ID/server.py
```

The authenticated Codex CLI still runs on the host. This option changes where
acceptance runs after it returns generated source. It does not move generation,
account authentication or model selection into Docker. Default reconstruction
and replay stay local. The [existing container policy](../container-runner.md),
pinned image, boundary probes and shared-kernel/cooperative-evaluator limits
remain unchanged.

## Behavior and evidence

1. Validate the requested container deadline and prepare the pinned Docker image
   before calling the generator. A missing runtime or failed image preparation
   returns exit code 2, with no model call and no reconstruction record.
2. Freeze the contract, verifier and predecessor before generation. During
   generation, the fresh workspace contains only the contract and response
   schema. The predecessor is staged only after the generator exits.
3. Send the frozen source bytes to Docker. Do not compile or load the verifier
   on the host in container mode. The runner checks source hashes, actual
   boundary observations and the completeness of any passing report.
4. Publish actual acceptance results with `execution: docker-container` and the
   requested mode in provenance. Genuine acceptance failures remain `failed`
   and can supply feedback to the bounded next attempt.
5. If runtime execution, timeout or report validation fails after generation,
   preserve the generated source, prompt, predecessor, frozen verifier/contract
   and provenance as `verification-error`. The report has zero checks and no
   acceptance verdict. Stop the cycle, return exit code 2 and never fall back to
   host execution or spend another model attempt on infrastructure feedback.

The audit rejects a `verification-error` record that claims check results. The
bilingual notebook shows a distinct runtime-error label, displays the error, and
uses the report's actual execution mode instead of always claiming local
execution. Existing evidence and outcomes are not relabelled. A retained valid
candidate can later be rechecked using archived replay without calling a model.

`--timeout` still bounds each generator attempt. `--verification-timeout`
separately bounds container execution (default 90 seconds, range 1..300).
Image preparation retains its separate bounded deadlines. The requested
verification mode is recorded even if generation itself fails; such a candidate
remains `not-executed`.

## Local verification

| Check | Observed result |
| --- | --- |
| Dependency-free unit suite | 39 passed, 2.868 seconds |
| Independent reference HTTP acceptance | 19/19 passed |
| Evidence audit | Passed |
| Maintained Python compilation and notebook JavaScript syntax | Passed |
| Real Chromium notebook suite | 8 passed, 12.952 seconds |
| Today's real AI reconstruction | Attempt 1 passed 19/19 |

The eight new adapter unit tests use synthetic generator responses and mocked
container execution. They check frozen input selection despite files changing
during generation, no host verifier load in container mode, refusal before model
use, source retention and no retry after runtime failure, bounded acceptance
retry with both records retained, generation-timeout provenance, unchanged
local default, CLI options/exit code, and rejection of runtime errors that claim
acceptance checks. These temporary fixtures are never public AI attempts.

The new browser test supplies synthetic HTTP responses for a container pass and
a runtime error. It checks English/Chinese labels, the error message, and the
absence of a false local-execution claim. The other seven browser checks still
exercise the real ledger and every published artifact. This uses Chromium
156.0.8078.4 and Playwright 1.64.0, with no new runtime dependency.

The actual local command `python3 -m phoenix rebuild --container --attempts 1
--verification-timeout 90` returned exit code 2 with:

```text
Docker is not installed; container verification did not run
```

No model was called and no ledger entry was created by that refusal. Actual
container execution is delegated to the existing Linux CI and Pages checks;
local mocks are not described as real containers.

## Linux CI controls

`python3 tests/container_smoke.py` retains the reference, latest archived AI,
original SQL-fixture archive and deliberate timeout/cleanup checks. It also
runs the real rebuild adapter with a synthetic executable replacing only the
Codex CLI. This fixture returns the latest archived AI source, checks that the
generation workspace contains only contract/schema, and calls no model. The
adapter then uses the real Docker runner for 19-check HTTP acceptance.

A separate injected `ContainerError` after fixture generation checks that the
adapter retains the source and produces one `verification-error` record without
a second generation. Its temporary ledger is audited and discarded. The public
ledger must remain byte-for-byte unchanged by these controls. The emitted
`rebuild_adapter_control` identifies the archived source, synthetic generation,
actual Docker observations and the injected error control explicitly. These
controls are not fresh AI reconstructions or additional model successes.

### Observed Linux result

The [completed container job](https://github.com/KeyOfTheQuestion/phoenix-rebuild/actions/runs/40257715512/job/121363056856)
checked commit `1e9672fc8b04d6e5536184bc4631d92669a1a354`. Its emitted
evidence timestamp is `2026-10-26T02:10:05.247431+00:00` (UTC). The complete
[parsed CI evidence](2026-10-26-container-rebuild-ci.json) retains the source
job, checked commit, individual check outcomes and actual container probes.

| Actual Docker subject | Result |
| --- | --- |
| Current reference and current verifier | 19/19 passed |
| Today's archived AI candidate and its recorded predecessor | 19/19 passed |
| Early AI candidate with its original SQL-fixture verifier | 19/19 passed |
| Rebuild adapter using a synthetic CLI response containing today's archived source | 19/19 passed |

The adapter control's candidate, contract, verifier and predecessor hashes match
the selected archive. Its injected runtime failure retained the same source,
recorded zero acceptance checks, and made exactly one synthetic generation call
despite allowing two attempts. Both temporary ledgers passed audit and the
public ledger was unchanged. Neither control called a model; the last row is
adapter integration evidence, not a second actual AI reconstruction.

All four real probes confirmed UID 65534, loopback-only networking, read-only
root/input mounts, restricted 64 MiB temporary storage, 256 MiB memory with no
additional swap, 128 PIDs and one CPU of quota. They observed no effective
capabilities, no new privileges, no Docker socket and no host sentinel variable.
The separate slow-candidate control hit its two-second deadline and left no
Phoenix test containers. These cooperative observations retain the documented
shared-kernel and shared-evaluator limits.

The [full CI workflow](https://github.com/KeyOfTheQuestion/phoenix-rebuild/actions/runs/40257715512)
passed, including Python 3.9/3.12/3.13 and Chromium checks. The feature commit's
[Pages publication](https://github.com/KeyOfTheQuestion/phoenix-rebuild/actions/runs/40257715582)
also succeeded. Today's real reconstruction remains the separate local record
below; later container verification does not change its execution method.

## Today's one actual reconstruction

The only real reconstruction cycle used the existing authenticated CLI, clean
default configuration, at most two attempts and a 300-second generator limit.
Attempt 1 passed, so no second attempt was made. The host has no Docker; this
actual reconstruction used local subprocess acceptance and is not relabelled by
later CI container checks.

- Record: [`20261026T020534Z-d08ffd`](../evidence/20261026T020534Z-d08ffd/report.json).
- HTTP acceptance started at `2026-10-26T02:05:34.161275+00:00` (UTC).
- Candidate SHA-256: `f6be34011759fc083dac477e8b635e93af56abc8359cb3b7205dbfa8db27a9cb`.
- Predecessor record: `20261024T023201Z-51147a`, the latest successful AI record
  available before this run.
- Predecessor SHA-256: `4b9710cda28f5272290dfd5d14967f15518684bcffb8af2a998cde4c38359476`.
- Previous data was created through the predecessor's HTTP API, then reused by
  the new implementation, including event identity and idempotency state.
- CLI: `codex-cli 0.163.0`; clean CLI default; resolved model identity unknown.
- Generation: 73.011 seconds; zero observed tool calls; no manual candidate edit.
- Actual returned usage: 22,790 input tokens, 3,799 output tokens, 459 reasoning
  output tokens, zero cached/cache-write input tokens. Monetary cost is unknown.

The contract and independent HTTP verifier remain unchanged. Historical failed
attempts are preserved. No model override, account permission change, purchased
credits or new credentials were used. No execution on October 25 is inferred
from today's record.
