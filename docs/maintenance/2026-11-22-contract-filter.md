# Filter notebook records by contract · 2026-11-22

The ledger now contains both 19-check contract-001 and 32-check contract-002
experiments. The new contract selector lets readers inspect each separately,
combining contract, source and exact outcome. For example,
`?contract=002&kind=ai-rebuild&status=passed` selects successful v2 AI attempts.
Rows without a contract field retain the notebook's existing 001 interpretation;
no historical record or acceptance verdict is edited. Overall metrics still
describe the complete ledger.

The selected contract survives a fresh visit, reload, language switch and
Back/Forward navigation. Returning to all contracts removes that query parameter
while retaining the other filters, evidence ID, unrelated parameters and fragment.
Unknown contract values use the default view. An evidence permalink can still
open a run excluded from the table. Cached markup without the new control keeps
the default view and the earlier filters. Filter controls wrap on narrow screens.

The local Chrome negative control first failed with `missing contract control`.
After implementation, it passed URL restoration, legacy mapping, all 12
contract/source/outcome combinations, unchanged overall counts, translated
labels, reset and history navigation. Three new Playwright tests cover filter
intersections and narrow-screen controls, fresh-context/reload links, and cached
markup. Existing tests also cover contract values in unknown links, evidence
permalinks and history navigation. Full browser results are available in the
CI and Pages workflow logs; local Chrome is a separate check.

Today's single bounded contract-002 reconstruction cycle used the existing
authenticated Codex CLI 0.166.0 with its clean default configuration. The first
attempt passed 32/32 on CPython 3.9.6 / SQLite 3.43.2. The v1 migration fixture
used `20261103T023452Z-4ec8a9`, as required for the composed acceptance. The CLI
reported 96.071 generation seconds, 24,836 input tokens, 4,421 output tokens and
424 reasoning output tokens. The resolved model and monetary cost are unknown.
[Reconstruction report](../evidence/20261122T022850Z-b8bf3f/report.json).

The separate eight-check v2 continuity fixture handed the November 21 candidate's
HTTP-created database to today's candidate and passed 8/8. Its frozen bundle is
outside the AI ledger and the 32-check reconstruction verdict.
[Continuity report](2026-11-22-continuity/report.json).

Before committing, all 164 unit tests passed on Python 3.9, independent
reference HTTP acceptance passed 19/19, and the evidence audit passed. Both
new bundle manifests and the continuity candidate/predecessor bindings were
checked against the actual archived bytes. Historical ledger rows and evidence
were unchanged.
