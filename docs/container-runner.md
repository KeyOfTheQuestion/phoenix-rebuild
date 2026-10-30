# Optional Docker acceptance runner

`container-verify` runs the existing HTTP acceptance harness and its candidate
inside one disposable container. The predecessor writes its database there
through HTTP before the candidate reopens it. Loopback remains available inside
the container; no host port is published and external networking is disabled.

Requirements: Python 3.9+ on the client, Docker with a Linux daemon using cgroup
v2, and registry access for the first image pull. A Docker Desktop Linux engine
can provide this on a development machine. The daemon must be able to mount the
client's temporary staging directory. Docker is optional for normal verification,
generation, archived replay and the static notebook.

```sh
python3 -m phoenix container-verify
python3 -m phoenix replay RUN_ID --container --timeout 90
python3 -m phoenix rebuild --container --verification-timeout 90
python3 -m phoenix container-verify \
  --candidate docs/evidence/RUN_ID/server.py \
  --predecessor docs/evidence/RUN_ID/predecessor.py \
  --timeout 90
```

The command returns JSON: exit 0 for passing acceptance, 1 for failed acceptance,
or 2 for an unavailable runtime, timeout, unconfirmed policy or invalid output.
It never falls back to host execution. First-use image preparation is separately
bounded (15 seconds per inspection, 120 seconds for a pull). Container execution
defaults to 90 seconds and accepts limits from 1 to 300 seconds. Cleanup requests
force removal of this invocation's uniquely named container with a 10-second
deadline, including after an execution timeout. If the daemon becomes unavailable,
check `docker ps -a --filter name=phoenix-verify-` when it recovers.

## Inputs and policy

For `container-verify`, the runner copies only `server.py`, the current independent `verifier.py`, its
small `entrypoint.py`, and optional `predecessor.py` into a temporary directory.
That directory is the only host bind mount, mounted read-only at `/input`.
The repository, home, Docker socket, account credentials and host environment
variables are not mounted or forwarded. The harness creates fresh synthetic
admin credentials inside the container for its HTTP tests.

For `replay RUN_ID --container`, ledger/manifest/report validation happens first.
Only the verified archive bytes for `server.py`, `verifier.py` and the recorded
`predecessor.py` (when present) are supplied to the same runner. The archived
verifier is not compiled or loaded on the host. Source files changed on disk
after validation cannot replace the bytes being executed. Without a recorded
predecessor, the archived verifier keeps its original fixture behavior; no
current reference implementation or current verifier is substituted.

Contract-002 `retention-composed-v1` archives also supply their verified
`baseline.py`, `retention.py`, `records.py`, `audit.py` and `contract.md`.
Only this exact dependency set is accepted, with a recorded predecessor and
immutable source bytes. The entrypoint and frozen modules load only inside
Docker, using temporary package directories on `/tmp`. Host validation checks
all eight source/contract bindings, the reference-only contract-002 profile,
schema-version-2 expectation, all 32 passing identifiers and the actual boundary
probe. The recorded `report.json` is audited on the host but is not mounted or
used as a substitute for fresh checks. Archive-format version 1 remains unchanged.

Container replay reports include `replay_run_id`, `replay_source`, contract and
verifier hashes as well as the container observations. A missing Docker runtime
or an inconsistent archive is refused without host execution. Local replay
without `--container` retains its existing behavior and does not require Docker.
See the [2026-10-30 controls and daily reconstruction](maintenance/2026-10-30-retention-container.md).

| Boundary | Policy |
| --- | --- |
| Image | Official Python 3.12 slim Bookworm, pinned by multi-platform manifest digest |
| User | UID/GID 65534; all Linux capabilities dropped; no new privileges |
| Filesystem | Read-only root and input mount |
| Writable storage | 64 MiB `/tmp` tmpfs with `nosuid,nodev,noexec` |
| Network | `none`; loopback only; no published ports |
| Memory | 256 MiB; no extra swap |
| CPU | 1 CPU |
| Processes/threads | 128 PIDs |
| File descriptors | 256 |

Image: `python:3.12-slim-bookworm@sha256:6a0e06f5ba0b373d051aabd3fffb9b873f27f9a2a3a14bcbe37dee6f0e3bbc70`.
This digest was resolved from the [official Python image metadata](https://hub.docker.com/v2/repositories/library/python/tags/3.12-slim-bookworm)
on 2026-10-23. Updating a mutable tag cannot silently change the pinned image.
See Docker's [run options](https://docs.docker.com/reference/cli/docker/container/run/)
and [resource limits](https://docs.docker.com/engine/containers/resource_constraints/).

Before acceptance, an in-container probe checks the actual UID, network
interfaces, mount permissions, tmpfs size/restrictions, capabilities,
`NoNewPrivs`, and cgroup-v2 memory, swap, CPU and PID limits. The result also
records absence of a Docker socket and a synthetic host-only environment marker.
The host validates those observations and binds returned source hashes to its
input snapshots. JSON includes the pinned image, inspected image ID, policy,
probe results and entrypoint hash. Failed runtime diagnostics stay in ignored
`.phoenix/container-errors/latest.log`; image-pull errors can also be inspected
there. This file can name local paths and is not part of public evidence.

## How this differs from a reconstruction record

These verification modes do not generate code, call a model, update
`docs/runs.json`, publish a reconstruction bundle or edit historical outcomes.
Rebuild and replay without `--container` retain local subprocess
execution. `container-verify` checks with the current harness; container replay
checks with the selected record's archived harness and predecessor/fixture.

`rebuild --container` is different: it calls the authenticated host generator and
publishes the resulting candidate with its actual Docker acceptance report.
The runtime and pinned image must be available before a model call. Verifier,
contract and predecessor snapshots are frozen before generation, and the latter
two code inputs are staged only after generation exits. The verifier is loaded
only inside Docker. `--timeout` limits generation; `--verification-timeout`
limits container execution (default 90, range 1..300 seconds). It does not move
the generator or its authentication inside the container.

A preflight refusal exits 2 without generating or publishing an attempt. If
container verification fails after generation, the source, prompt, provenance
and predecessor are preserved as `verification-error`, with zero checks and no
acceptance verdict. The cycle stops and exits 2 without host fallback or another
model call. A genuine HTTP acceptance failure remains `failed` and may supply
feedback to a bounded retry. The daily run on this host stays local while Docker
is unavailable; no historical execution mode is rewritten.

`python3 tests/container_smoke.py` performs real Docker integration checks in
Linux CI and before Pages publication: reference acceptance, the latest audited
AI candidate through `replay --container`, an early AI record with a different
archived SQL-fixture verifier, and a deliberate two-second timeout
followed by a check for leftover test containers. No model credentials are used
by these CI checks. It also exercises `rebuild --container` through a synthetic
CLI fixture returning an existing AI archive, with real Docker acceptance and
a separate injected runtime-failure control. Its temporary ledger is audited
and discarded; the public ledger is unchanged and no new AI success is claimed.
Unit tests use a fake Docker client and are labeled as such. See the
[2026-10-26 adapter verification record](maintenance/2026-10-26-container-rebuild.md).

## Limits

This profile reduces host exposure and bounds resources for cooperative
reconstruction experiments. Docker shares the host kernel; this is not a VM or
a certification for arbitrary hostile code. The verifier and candidate share a
container and UID, so it does not establish adversarial benchmark integrity.
The container can read the explicitly staged predecessor and verifier after
generation. Source blindness during generation remains a separate procedural
claim. Use a disposable machine without secrets for genuinely hostile subjects.
