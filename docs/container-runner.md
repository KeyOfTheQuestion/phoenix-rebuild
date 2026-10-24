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

Container replay reports include `replay_run_id`, `replay_source`, contract and
verifier hashes as well as the container observations. A missing Docker runtime
or an inconsistent archive is refused without host execution. Local replay
without `--container` retains its existing behavior and does not require Docker.

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
Daily generation and replay without `--container` retain local subprocess
execution. `container-verify` checks with the current harness; container replay
checks with the selected record's archived harness and predecessor/fixture.

`python3 tests/container_smoke.py` performs real Docker integration checks in
Linux CI and before Pages publication: reference acceptance, the latest audited
AI candidate through `replay --container`, an early AI record with a different
archived SQL-fixture verifier, and a deliberate two-second timeout
followed by a check for leftover test containers. No model credentials are used
by these CI checks. Unit tests use a fake Docker client and are labeled as such.

## Limits

This profile reduces host exposure and bounds resources for cooperative
reconstruction experiments. Docker shares the host kernel; this is not a VM or
a certification for arbitrary hostile code. The verifier and candidate share a
container and UID, so it does not establish adversarial benchmark integrity.
The container can read the explicitly staged predecessor and verifier after
generation. Source blindness during generation remains a separate procedural
claim. Use a disposable machine without secrets for genuinely hostile subjects.
