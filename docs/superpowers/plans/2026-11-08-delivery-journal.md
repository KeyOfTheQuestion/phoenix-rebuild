# Delivery journal implementation plan

> **For agentic workers:** Execute inline with superpowers:executing-plans. The recurring task already authorizes implementation, verification and publication in the named checkout.

**Goal:** Retain local delivery attempts across a client interruption without claiming a receiver outcome or resending anything.

**Architecture:** An optional SQLite journal commits an unconfirmed row before network activity. The parent later stores a filtered final receipt. A read-only history command has no delivery side effects. This is an attempt log, not a durable queue.

**Tech stack:** Python 3.9+, SQLite and standard library only.

**Spec:** Roadmap item 8 and the journal section added to `docs/delivery.md` with this implementation.

## Global constraints

- Preserve historical evidence and keep local receipts outside the AI ledger.
- Existing IPv4 loopback restrictions, single POST and worker deadline remain.
- No URLs, tokens, request/response bodies or raw exceptions in stored rows.
- Commit before network; reject an unavailable or unrelated database before GET.
- An interrupted row remains unconfirmed; history never retries or infers failure.

## Review focus

- Parent interruption after receiver capture must leave an unconfirmed row.
- Final-write failure must retain the observed HTTP outcome and expose storage failure.
- Concurrent clients must create distinct durable rows.
- An unrelated SQLite file must remain untouched.
- Read-only history must neither create files nor contact services.

## One implementation task

**Files:** Add `phoenix/delivery_journal.py` and `tests/test_delivery_journal.py`; extend the delivery client, CLI and smoke runner. Update bilingual README, notebook, roadmap and method notes.

**Interfaces:** `deliver_event(..., journal=None)`; `start_attempt(path, metadata) -> id`; `finish_attempt(path, id, receipt)`; `delivery_history(path, limit=20) -> list`. CLI: `deliver-event --journal FILE` and `delivery-history --journal FILE --limit 20`.

- [x] Write nine real SQLite/HTTP tests for redacted receipts, refusal before network, bounded read-only history, process interruption, concurrency, a locked final write, simultaneous initialization, an unrelated hot rollback journal and WAL refusal.
- [x] Observe missing journal behavior fail before implementation (missing-module import); the additional review regressions also exposed unsafe first-use and database handling.
- [x] Implement journal schema identity, FULL synchronous transactions, private new-file permissions and bounded lock waits. Whitelist receipt fields.
- [x] Integrate pre-network start and final persistence; a failed final write returns `journal_saved: false` and CLI exit 2 without losing the HTTP result.

## Review rulings

- The named checkout is clean and the recurring instruction explicitly authorizes pushing to main; execute here without creating an unrelated worktree.
- A fresh reviewer reproduced a first-use race, unrelated hot-journal recovery and WAL sidecar creation. Publish a fully initialized private database with an exclusive same-directory hard link; inspect the raw SQLite identity/version/mode header before any SQLite open. WAL is refused. This targets trusted local files, not adversarial file replacement or all filesystems.
- Local attempt logs are retained indefinitely until their owner manages the file. This iteration adds no queue, recovery resend, deletion or destination-policy expansion.
- [x] Run focused tests and an expanded AI-inbox smoke on installed Python 3.9/3.14; preserve source snapshots and all observations (21/21 on both).
- [x] Run the full unit suite, independent HTTP acceptance and evidence audit (132 tests on both, 19/19 HTTP, audit passed). The sole real generation passed 32/32 on attempt 1; latest-v2 continuity passed 8/8.
- [x] Update public documentation. After committing, push and verify CI, Pages and live artifact bytes as the recurring task requires; final outcomes are available from that commit's Actions runs.
