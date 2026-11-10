# Delivery history pagination implementation plan

> **For agentic workers:** Execute inline with superpowers:executing-plans. The recurring task authorizes implementation, verification and publication in the named checkout.

**Goal:** Browse delivery journals beyond the newest 100 rows, with read-only continuation and no recovery resend.

**Architecture:** Select up to `limit + 1` rows ordered by `(started_at DESC, id DESC)`. Return at most `limit` rows and an unsigned opaque cursor for the last visible key only when another row exists. Continue with a strict less-than tuple comparison. Preserve the legacy Python list API.

**Tech stack:** Python 3.9+, standard library, existing SQLite schema.

**Spec:** Roadmap item 8's local attempt storage; pagination section added to `docs/delivery.md` with this change.

## Constraints and review focus

- Pages return 1..100 rows (default 20); no schema migration, record mutation or HTTP call.
- CLI adds `--cursor TOKEN` and additive `next_cursor`; Python adds `delivery_history_page(path, limit=20, cursor=None)`.
- Cursor: unpadded URL-safe base64 of a compact JSON array `[1, started_at, id]`, at most 256 characters. Validate version/type/shape, printable ASCII key length 1..64 and a 32-character lowercase hex ID. Reject malformed/noncanonical encodings before opening a journal, without echoing inputs.
- Equal timestamps must use ID as tie-breaker. Newer inserts must not shift continuation; finalized receipts may update in place while ordering stays fixed.
- This is an unsigned seek position, not a security token or a transaction snapshot. Cross-page changes, late commits and externally edited/backdated rows are not hidden by a snapshot guarantee.
- Limit returned rows, not sorting work or total scan latency. Keep existing SQLite/file assumptions and history refusal behavior.

## One implementation task

**Files:** Extend `phoenix/delivery_journal.py`, CLI and smoke; add `tests/test_history_pagination.py`; extend D01 to read an actual AI-backed delivery receipt with the page API. Update bilingual README/notebook and dated evidence.

- [x] Add H01..H08 for traversal beyond 100, timestamp ties, newer insertion, finalization between pages, invalid cursor refusal, terminal/empty pages, CLI continuation/redaction and legacy compatibility. Observe the missing page API fail.
- [x] Implement bounded cursor validation and keyset query; retain read-only database opens and legacy list output.
- [x] Fresh-context review found two test gaps, not a production defect. Strengthen H05 with an instrumented database boundary, cursor-specific refusal and noncanonical JSON/base64 cases; prove both encoding guards with negative controls.
- [x] Run full unit checks: retain initial floor failure and subsequent 149/149 serial pass; Python 3.14 passed 149/149. Current HTTP acceptance passed 19/19 and both client smokes passed 38/38. Freeze sources/reports and audit before commit.
- [x] The sole real reconstruction passed 32/32 on attempt 1; latest-v2 handoff passed 8/8 and both frozen AI runtime replays passed 32/32.
- [x] Update docs and retained failure metadata. After commit, push/check CI/Pages and compare live artifact bytes as the recurring task requires; remote outcomes are available from that commit's Actions runs.

## Rulings

- Work in the clean user-named checkout and push main as explicitly authorized; no additional execution/worktree approval prompt.
- Preserve old journal schemas and artifacts. Cursor contents reveal only a key already visible in local history; cursor validity grants no authorization or journal identity guarantee.
- The first Python 3.9 full suite failed the existing frozen November 2 matrix replay (exit 1, empty stderr). Its original assertion omitted stdout, so the failed backend check is unknown. An isolated diagnostic of the same verified bytes passed 19/19; do not infer a cause or change historical source/verdicts. Add stdout to that assertion's failure diagnostic and repeat the floor suite serially, retaining both observations.
