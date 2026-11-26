# Explicitly index an existing delivery journal · 2026-11-26

`delivery-journal-index --journal FILE` adds yesterday's optional history
order index to an existing supported journal. This is an explicit write
operation; ordinary delivery and read-only history retain their earlier
behavior and do not automatically upgrade existing files.
[Command and limits](../delivery.md#index-an-existing-delivery-journal).

The command validates the journal through the existing database helper,
then uses `BEGIN IMMEDIATE` to serialize the existence check and index build.
The result reports `created: true` after a successful commit, or `false` when
the canonical index already exists. It never creates a missing journal or
connects to a receiver. A same-name index is inspected for table, key order,
binary collation, uniqueness and partial predicates; an unexpected definition
is refused without replacement. Existing rows, receipts and journal version
are preserved. Index building can hold up writers; its duration is not bounded
by the helper's one-second lock-wait setting.

The initial real-CLI test failed because the command did not exist. H25..H30
now cover complete logical-row and receipt preservation, removal of temporary
sorting, repeated and concurrent requests, definition collisions, CLI result
semantics, missing/unrelated/WAL files, held writer locks and failed commit.
Three concurrent upgrades yield one creation and two existing-index results.
No test sends an event as a consequence of this command.

An independent review found no production blocker and tested transaction and
collision behavior. It suggested stronger commit-failure coverage. The first
trace assertion exposed a test-fixture difference: on CPython 3.9.6 / SQLite
3.43.2, the helper's readonly reader produced `disk I/O error` at the writer's
`BEGIN IMMEDIATE`, before index creation. The same readonly fixture reached
commit failure on CPython 3.14.6 / SQLite 3.53.3. The local failed trace and
comparison logs are retained; no backend acceptance record was changed.

The final portable fixture uses a regular SQLite connection holding only a
read transaction. It traces actual `CREATE INDEX` and attempted `COMMIT`,
observes refusal, checks unchanged database bytes and no leftover sidecars,
then verifies a later upgrade reports creation. Explicit `ROLLBACK` SQL is not
required: CPython versions differ in whether rollback is traced or performed
implicitly when the connection closes. This fixture passed on both installed
Python 3.9 and 3.14 runtimes. The production transaction logic was unchanged.

Today's single bounded contract-002 reconstruction used the existing
authenticated Codex CLI 0.166.0 with its clean default. The first attempt
passed 32/32 on CPython 3.9.6 / SQLite 3.43.2, using the successful v1 AI
candidate `20261103T023452Z-4ec8a9` for the migration fixture. The CLI reported
90.217 generation seconds, 23,898 input tokens, 4,082 output tokens and 129
reasoning output tokens. The resolved model and monetary cost remain unknown.
[Reconstruction report](../evidence/20261126T023550Z-37f2ed/report.json).

The separate handoff from the November 25 candidate to today's candidate
passed 8/8 using HTTP-created data/policies and disclosed synthetic timestamp
aging. Its bundle remains outside the AI ledger and 32-check verdict.
[Continuity report](2026-11-26-continuity/report.json).

On the final test fixture, all 175 unit tests passed on Python 3.9, independent
reference HTTP acceptance passed 19/19, and the evidence audit passed. The full
client smoke invoked from outside the checkout passed all 64 checks using
today's actual AI inbox. H25..H30 are client maintenance observations, separate
from the backend's reconstruction checks. The source snapshots bind the tested
CLI, journal implementation and regression fixtures.
[64-check client report](2026-11-26-delivery-sources/python39.json),
[source manifest](2026-11-26-delivery-sources/manifest.json).
