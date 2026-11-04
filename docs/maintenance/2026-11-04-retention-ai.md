# 2026-11-04: first contract-002 AI reconstruction

`rebuild --contract 002` now generates a complete implementation of the
migration/retention contract and evaluates it against all **32 frozen checks**.
This completes the AI-generation follow-up in roadmap item 5. Contract 001
remains the default; reference verification retains its separate classification.

## One actual bounded model cycle

```sh
python3.9 -m phoenix rebuild --contract 002 --attempts 2 --timeout 300 \
  --predecessor docs/evidence/20261103T023452Z-4ec8a9/server.py
```

The command above illustrates selecting an installed Python 3.9. This host used
its installed system interpreter, CPython **3.9.6 / SQLite 3.43.2**, with existing
authenticated `codex-cli 0.145.0`, `--ignore-user-config`, no model override and
at most two attempts, 300 seconds each. **Attempt 1 passed 32/32**; no second
model call was made. The generated candidate was not manually edited.

| Actual observation | Value |
| --- | --- |
| AI record | [`20261104T024607Z-2c2c04`](../evidence/20261104T024607Z-2c2c04/report.json) |
| Acceptance began (UTC) | 2026-11-04 02:46:05.110984 |
| Generation duration | 120.509 seconds |
| Baseline / retention | 19/19 and 13/13 passed |
| Observed input / output / reasoning tokens | 18774 / 6366 / 900 |
| Cached input / cache-write input tokens | 0 / 0 |
| Observed tool calls | 0 |
| Resolved model identity / monetary cost | Unknown / unknown |

The predecessor was the latest successful contract-001 AI implementation,
[`20261103T023452Z-4ec8a9`](../evidence/20261103T023452Z-4ec8a9/server.py).
It created the migration data through real HTTP. The 32-check acceptance captures
both sources once, uses separate baseline/retention databases, and compares all
v1 logical rows before aging any synthetic expiry fixture. Fixture aging uses
explicit SQLite timestamp changes while stopped; it is not elapsed-time evidence.

## Frozen package and method change

AI archives use **`retention-ai-v1`**, distinct from the reference-only
`retention-composed-v1` profile. A small frozen entrypoint wraps the same composed
checks with AI metadata; the complete 32-check pass rule is unchanged. Generation
freezes the contract, entrypoint, four helper modules and predecessor before the
provider runs. Only the contract/output schema are staged in the fresh generation
directory; no reference, predecessor or verifier is supplied to the model.
This remains procedural separation for a cooperative agent, not a hostile-agent
sandbox or signed proof of source blindness.

The [manifest](../evidence/20261104T024607Z-2c2c04/manifest.json) binds ten artifacts,
including the [exact prompt](../evidence/20261104T024607Z-2c2c04/prompt.md), generated
source and report. Audit and replay require the exact dependency set, matching
AI kind/provenance, all bindings and a complete passing check set.

| Frozen input | SHA-256 |
| --- | --- |
| Candidate | `d7b92cc16b080b1d4dfc993b8641bb450a901849167e1ad9459a8e7da01c2960` |
| Predecessor | `e2b2f0485fd8ebccee134abe2aea93c35046bfb5e25d75ac287ad16442da22d3` |
| Contract | `2dce08ea1ee5becc981d9537595d925a89bbed9c6acb901239a58c5eaabcb80b` |
| AI entrypoint | `f6429958cf2f3f9d00ddb8601f5c86a4ba0767046b4fc15361366fc3165596c5` |
| Baseline checks | `494d66e9c3e66b33596ebb0ba55e68d3db32347bc1e658c3bc1f0a87f3ebe0b1` |
| Retention checks | `a9539c851a927cb7c696193cda1c456a603db0ab95d7b87c6f06419f49763f75` |
| Records helper | `ffd8cb2e5d8542a461a3cd80f2b0f77f1c890ee5745b05e4f6e016d00fa7740b` |
| Audit helper | `71b3767736b3d2c037ddcff76397c689cca094a30479c7fb979706c327b11345` |

Contract-002 text drops its previous reference-only pilot label and documents
AI-generation eligibility. Its version remains 2.0.0: endpoint/schema requirements
are unchanged, but the exact text hash changes. The shared baseline/retention
checks and reference implementations are unchanged today; the AI entrypoint and
audit support differ from the earlier reference archive. All historical contract
snapshots, generated failures and verdicts remain untouched.

Post-generation review reproduced an audit gap: removing or emptying the
entrypoint's report digest could bypass that one binding, even though its
manifest digest was valid. Three negative subcases failed before the fix and
passed afterward. The maintained audit now requires the verifier report binding
for both v2 profiles, retaining legacy v1 behavior. Controls also cover incorrect
digests and omission of each of the eight bindings. The real bundle retains its
original pre-hardening `audit.py` snapshot and hash above; it is not replaced with
the later maintained audit. The composed evaluator uses that helper's unchanged
baseline check identifiers. No candidate, verdict or model call changed for this
audit repair.

Generation failure now retains a complete AI v2 bundle with zero checks and
`not-executed`. An ordinary local evaluator exception preserves the generated
bytes as `verification-error` / `local-subprocess-attempted`, produces no
acceptance verdict and stops without another model call or mode switch.
Private diagnostics are not published. An actual acceptance failure can still
provide only failed-check summaries to the bounded next attempt.

## Verification

- Full dependency-free unit suite after audit hardening: **94 passed** on Python
  3.9.6 (53.962 seconds) and **94 passed** on Python 3.14.6 (27.743 seconds).
- Independent reference HTTP acceptance: **19/19 passed**; public evidence audit
  passed after publication of the new local bundle.
- [Actual frozen AI archive matrix](2026-11-04-ai-v2-matrix.json): **32/32 passed**
  on both CPython 3.9.6 / SQLite 3.43.2 and CPython 3.14.6 / SQLite 3.53.3.
  Both received identical captured source bytes; no model call, ledger append
  or archive change occurred during replay. Two versions do not establish full
  Python/SQLite compatibility.
- Eight new adapter controls cover complete input freezing during generation,
  AI/reference profile separation, feedback-only retry, timeout/invalid-Python
  preservation, local evaluator failure, pre-call container refusal and CLI
  selection. Synthetic fixtures are temporary and never enter the public ledger.
- Local synthetic-CLI control returned the existing reference, ran real 32-check
  acceptance and audited a disposable AI-profile archive without calling a model.
  Existing Linux CI/Pages additionally run its real Docker replay. They also
  replay the actual new AI archive, preserving its kind, eight source bindings,
  32-check verdict, measured container runtime and actual boundary observations.
- Existing matrix smoke now checks v1 AI, v2 reference and v2 AI records separately.
  A new Chromium control covers AI v2 passed/failed/generation-failed rows, AI
  metrics and all dependency/prompt links. Reference rows remain outside AI metrics.

This host has no Docker or optional Playwright installation. Remote Docker and
browser outcomes are reported by the existing CI/Pages workflows; no local run
of either is claimed. Existing SQLite resource warnings on newer Python remain
outside this feature and are not described as warning-free behavior.

## Remaining boundaries

Contract-002 generation currently uses **local acceptance only**. Combining it
with `--container` refuses before any model call; completed AI v2 archives support
local, Docker and installed-runtime matrix replay. Local execution retains normal
user permissions and network access.

The migration fixture currently needs a successful **v1 predecessor**. It proves
the checked v1-to-v2 migration, not v2-to-v2 retention-policy/data continuity.
Roadmap items 10/11 retain that fixture and container-generation integration as
separate bounded follow-ups. No model ranking, success streak, invented cost,
credit purchase, new credential or account/model permission change is involved.
