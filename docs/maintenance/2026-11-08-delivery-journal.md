# 2026-11-08 persistent local delivery history

Roadmap item 8 now has an optional local attempt log. `deliver-event --journal
FILE` commits an unconfirmed row before network activity and later saves a
filtered final receipt. `delivery-history --journal FILE --limit 20` reads it
without sending anything. The [client specification](../delivery.md#optional-local-attempt-journal)
defines the states, stored fields, supported filesystems and exit codes.

An interrupted process cannot reliably say whether a receiver accepted the
POST. Its row stays `unconfirmed`, with no fabricated attempt count or failure.
If only final persistence fails, stdout retains the observed HTTP result with
`journal_saved: false` and exit 2. Reading history never resends a row. This
iteration implements durable attempt storage, not an outbox or automatic retry.

## Actual client observations

The expanded smoke uses today's real AI inbox,
[`20261108T023025Z-c3520d`](../evidence/20261108T023025Z-c3520d/report.json),
with synthetic source events and independent loopback receivers. Both
[Python 3.9.6 / SQLite 3.43.2](2026-11-08-delivery-sources/python39.json) and
[Python 3.14.6 / SQLite 3.53.3](2026-11-08-delivery-sources/python314.json)
passed **21/21**. The original D01..D12 checks remain; nine new checks cover:

| Check | Observed behavior |
| --- | --- |
| J01 | Persist delivered, rejected and source-error receipts; omit tokens, URLs, bodies and query values; a new file has mode 0600. |
| J02 | Refuse unavailable or unrelated journals before either service receives a request; preserve existing database bytes. |
| J03 | Read bounded newest-first history without creating a missing file, modifying the journal or sending another POST. |
| J04 | Kill an actual CLI/worker process group after receiver capture; retain an unconfirmed row, without resending. |
| J05 | Competing clients retain distinct attempts in an existing journal. |
| J06 | A real SQLite write lock prevents final persistence; stdout still records the accepted POST, exits 2 and leaves the row unconfirmed. |
| J07 | Eight simultaneous first-use clients see only a fully initialized database and retain all eight receipts. |
| J08 | Refusing an unrelated database with a real interrupted transaction preserves both its database and hot rollback-journal fingerprints. |
| J09 | Refuse a WAL-mode journal before history or delivery can create sidecars or contact services. |

The [snapshot manifest](2026-11-08-delivery-sources/manifest.json) binds the
inbox, delivery worker, journal, HTTP helper, two check modules, runner, CLI,
record support and both reports. These are observations of the matching
checkout, not a standalone archive replay package. D12 remains explicit worker
protocol fault injection; the other checks use actual HTTP, SQLite or processes.
No model call is made by the delivery smoke, and it asserts that the public AI
ledger and measured source files remain unchanged. Existing CI and Pages run
the expanded smoke through their prior publication workflows.

## Development review and method changes

The initial tests first encountered the missing journal module. A fresh-context
code review then reproduced three concrete issues in the draft: publication of
an empty database during first use, recovery writes when refusing an unrelated
hot database, and possible WAL sidecars despite a read-only SQLite open.

The implementation now initializes a private database and publishes it with an
exclusive hard link. It checks the raw SQLite identity, version and rollback-mode
header before any SQLite open. Unsupported WAL files are refused. The added
first-use and WAL regressions failed against the draft and passed after these
changes. A separate in-memory omission of the header guard made J08 detect a
changed unrelated database and removed rollback journal; the unchanged final
implementation passed that same check.

The first interrupted-transaction fixture produced a cold journal, so it was
replaced with a larger spilled transaction and a verified hot header. An initial
negative-control run was stopped while formatting a large byte difference;
comparing file SHA-256 values completed the same negative control promptly.
Private development logs retain these outcomes. None is represented as an AI
generation attempt or a new backend acceptance check.

## Verification

The complete dependency-free unit suite passed **132 tests** on CPython 3.9.6
in **66.558 seconds**, and **132 tests** on CPython 3.14.6 in **30.058 seconds**.
Independent reference HTTP acceptance passed **19/19** and the evidence audit
passed. Existing newer-Python SQLite resource warnings remain disclosed.
This host has no Docker or optional Chromium installation; the established
remote container and browser jobs provide those checks before publication.

The [implementation plan](../superpowers/plans/2026-11-08-delivery-journal.md)
records the bounded scope and review rulings. This adds no backend route,
contract change, generation-model override or reference implementation change.

## Today's one bounded reconstruction cycle

Existing authenticated **codex-cli 0.166.0**, clean defaults and installed
**CPython 3.9.6 / SQLite 3.43.2** produced the sole real cycle. The limit was
two attempts of at most 300 seconds each. **Attempt 1 passed 32/32**, so no
second model call was made. Generated source was not manually edited.

| Actual observation | Value |
| --- | --- |
| AI record | `20261108T023025Z-c3520d` |
| Acceptance began (UTC) | 2026-11-08 02:30:24.006884 |
| Generation duration | 81.971 seconds |
| Observed input / output / reasoning tokens | 22999 / 3770 / 92 |
| Cached / cache-write input tokens | 0 / 0 |
| Observed tool calls | 0 |
| Resolved model identity / monetary cost | Unknown / unknown |

The 32-check migration fixture still requires the latest eligible v1 AI
predecessor, `20261103T023452Z-4ec8a9`. The separate
[v2 continuity bundle](2026-11-08-continuity/report.json) uses yesterday's latest
successful v2 AI, `20261107T023127Z-4a21fe`, and passed **8/8** with disclosed
synthetic fixture aging. The [frozen AI runtime matrix](2026-11-08-ai-matrix.json)
passed **32/32** on both installed runtimes. These replays made no model calls.
The public AI ledger gained exactly one real attempt; prior rows and artifacts
retain their original bytes and outcomes.

## Limits

Delivery still accepts only explicit IPv4 loopback HTTP destinations. The
journal is local, grows until its owner manages storage, and is neither an
encrypted store nor an independently signed observation. URL hashes are not
secrecy guarantees for guessable URLs. Existing permissions are not rewritten.
Trusted local files and a filesystem supporting hard links are required; this
is not a defense against adversarial path replacement or every storage failure.
History refuses WAL rather than silently changing database mode.

`finished` means a receipt was stored, not necessarily that delivery succeeded.
An unconfirmed row can describe an active client or one stopped before or after
POST. Explicitly invoking delivery again creates another attempt and may send a
duplicate. Destination policy beyond loopback, durable queues, recovery and
receiver-side idempotency remain future work.
