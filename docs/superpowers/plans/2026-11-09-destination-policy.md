# Destination policy implementation plan

> **For agentic workers:** Execute inline with superpowers:executing-plans. The recurring task already authorizes implementation, verification and publication in this named checkout.

**Goal:** Let a local delivery invocation require an explicit receiver allowlist before any network or journal operation.

**Architecture:** Read a bounded regular UTF-8 JSON file once. Validate its complete schema and every loopback destination, then match the exact selected URL. Bind the captured bytes to returned/persisted final receipts with SHA-256. Keep legacy invocations without a policy and the journal schema unchanged.

**Tech stack:** Python 3.9+ standard library only.

**Spec:** Roadmap item 8; destination-policy section added to `docs/delivery.md` with this change.

## Global constraints

- Existing literal IPv4 loopback HTTP, no redirects, one POST, worker deadlines and header/body limits remain.
- Policy: at most 16,384 bytes, regular file, UTF-8 JSON object with exactly `schema_version: 1` and `destinations` containing 1..16 distinct valid URL strings.
- Reject duplicate JSON keys, unknown fields, malformed entries and unmatched destinations before network or journal creation. Errors never echo file contents, paths or destinations.
- Match exact strings, including port, path, query and escaping; no wildcard or normalization rules.
- Capture once; edits affect the next invocation. Returned final receipts include only `destination_policy_sha256`, not a policy path or URLs. Interrupted journal rows retain their existing format and do not establish which policy was used.
- Keep historical evidence and AI metrics intact. No provider changes or model guesses.

## Review focus

- Deny paths/queries that nearly match an approved destination.
- Invalid unused entries or duplicate keys must not hide behind a valid selected entry.
- File changes after approval must not alter the captured decision or reported hash.
- Missing/nonregular/oversized inputs must refuse without HTTP or journal side effects.
- Policy opt-in must not expand address permissions or imply an OS enforcement boundary.

## One implementation task

**Files:** Add `phoenix/delivery_policy.py` and `tests/test_delivery_policy.py`; extend delivery/CLI, the journal receipt whitelist, AI inbox smoke, documentation and notebook.

**Interfaces:** `deliver_event(..., policy=None)`; `authorize_destination(path, destination) -> str` returns a SHA-256 or raises `DeliveryError`. CLI: `deliver-event --destination-policy FILE`.

- [x] Add eight real HTTP/file/SQLite tests K01..K08 and an actual-inbox protected delivery; observe missing behavior fail before implementation (unsupported policy argument).
- [x] Implement bounded read, strict schema and exact matching; validate before journal/network and add the captured hash to every returned final receipt before journal persistence.
- [x] Run focused tests and one fresh code review; the review found no actionable defect. An additional descriptor-lifetime check exposed a real directory-refusal leak; add K09 and close the raw descriptor in a finally block.
- [x] Run 141-unit suite on installed Python 3.9/3.14, independent HTTP acceptance and expanded 30-check delivery smoke; preserve snapshots and observations. Run the required audit before commit.
- [x] The only bounded real reconstruction cycle passed 32/32 on attempt 1. Latest-v2 continuity passed 8/8; frozen AI bytes passed 32/32 on both installed runtimes.
- [x] Update bilingual README/notebook and dated method record. After committing, push and check CI/Pages and live artifact bytes as the recurring task requires; final remote outcomes are available from that commit's Actions runs.

## Rulings

- Work in the clean, user-named checkout and push main as the recurring instruction explicitly requests. No extra worktree or execution-approval prompt.
- The allowlist is an optional per-invocation guard for trusted local files, not an account-wide security control. It adds no external hosts, recovery resend or durable queue.
- K09 uses actual POSIX descriptor counts and repeated directory/FIFO refusals. This host and CI are POSIX; arbitrary filesystem I/O has no absolute timeout guarantee, while captured bytes remain bounded.
