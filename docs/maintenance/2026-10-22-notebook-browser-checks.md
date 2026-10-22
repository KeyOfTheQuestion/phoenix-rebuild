# 2026-10-22: verify the public notebook in a real browser

Roadmap item 4 is implemented: seven Chromium checks exercise the actual static
notebook under a Pages-style project path. CI runs them alongside the existing
backend checks, and the existing Pages workflow runs them before publication.
The service and independent HTTP verifier retain their Python 3.9+ standard-library
runtime; browser test tools are optional and require Python 3.10+.

## What is exercised

- Metrics agree with the real published ledger, with reference checks excluded
  from AI totals.
- The AI-only and all-record filters change the visible rows and pressed state.
- Switching to Chinese updates the page and survives a reload; switching back
  restores English.
- Every record, including the retained generation failure, opens its own report
  dialog with the expected check count and candidate hash. All displayed artifact
  URLs return the bytes named by that record's manifest. Report links are also
  opened through real browser clicks.
- Contract navigation displays the complete published contract and returns to
  the notebook.
- An unavailable ledger displays a visible explanation. A failed report request
  leaves its button usable, and retrying after the response recovers opens the
  dialog.

The suite uses a temporary local HTTP server bound to an assigned port, the
`/phoenix-rebuild/` project prefix, isolated browser contexts and bounded waits.
Synthetic 503/404 responses exercise loading failures. No generated service is
executed by these browser tests. They are notebook verification, not additional
reconstruction attempts or changes to the 19 backend acceptance checks.

## Reproduce

Follow [the browser setup instructions](../setup.md#notebook-browser-checks), then:

```sh
.phoenix/browser-env/bin/python tests/browser_smoke.py
```

Pinned tool: [Playwright 1.64.0](https://pypi.org/project/playwright/1.64.0/).
The browser binary is installed using the matching package's CLI, following the
[official browser installation guide](https://playwright.dev/python/docs/browsers).
The suite starts and cleans up its own local server and browser.

- [Test source](../../tests/browser_smoke.py) SHA-256: `9a728a0eb8c3eb9358c6de9ef3d2aca5088f8d2f0fe7f7d36eb2c02616de8543`.
- [Optional requirements](../../requirements-browser.txt) SHA-256: `b79d27c688ba60a4755d7feac8445059447be696ad724c61dbe2e8d45de24005`.
- Local browser: Chromium 156.0.8078.4, Playwright 1.64.0, Python 3.12.14.
- Dataset at this run: 7 published records and 44 displayed archive links (including manifests).

Actual local output, after the notebook documentation update:

```text
Browser: Chromium 156.0.8078.4; Playwright 1.64.0
test_contract_navigation_loads_the_published_specification (__main__.NotebookBrowserTests.test_contract_navigation_loads_the_published_specification) ... ok
test_every_evidence_dialog_links_to_the_matching_archived_bytes (__main__.NotebookBrowserTests.test_every_evidence_dialog_links_to_the_matching_archived_bytes) ... ok
test_filters_switch_between_all_records_and_ai_only (__main__.NotebookBrowserTests.test_filters_switch_between_all_records_and_ai_only) ... ok
test_language_switch_persists_after_reload (__main__.NotebookBrowserTests.test_language_switch_persists_after_reload) ... ok
test_metrics_match_the_published_ledger (__main__.NotebookBrowserTests.test_metrics_match_the_published_ledger) ... ok
test_unavailable_ledger_shows_an_actionable_error (__main__.NotebookBrowserTests.test_unavailable_ledger_shows_an_actionable_error) ... ok
test_unavailable_report_allows_a_retry (__main__.NotebookBrowserTests.test_unavailable_report_allows_a_retry) ... ok

----------------------------------------------------------------------
Ran 7 tests in 8.169s

OK
```

## Negative control: a broken filter must fail

In a disposable copy of `docs/`, the real filter handler was changed from
`filter=button.dataset.filter;` to `filter="all";`. The filter test detected
that AI-only selection still showed all seven records. Its expected count came
from the real ledger, not a fixed number embedded in the test.

This command reproduces the control from the repository root:

```sh
.phoenix/browser-env/bin/python - <<'PY'
import importlib.util
from pathlib import Path
import shutil
import tempfile
import unittest

spec = importlib.util.spec_from_file_location('notebook_browser_checks', 'tests/browser_smoke.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
with tempfile.TemporaryDirectory() as temporary:
    site = Path(temporary) / 'docs'
    shutil.copytree('docs', site, ignore=shutil.ignore_patterns('__pycache__'))
    script = site / 'app.js'
    source = script.read_text()
    trigger = 'filter=button.dataset.filter;'
    assert source.count(trigger) == 1
    script.write_text(source.replace(trigger, 'filter="all";'))
    module.SITE = site
    suite = unittest.TestSuite([
        module.NotebookBrowserTests('test_filters_switch_between_all_records_and_ai_only')
    ])
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    assert result.testsRun == 1 and len(result.failures) == 1 and not result.errors
PY
```

Actual failure excerpt from the control (the assertion failure is expected):

```text
AssertionError: Locator expected to have count '5'
Actual value: 7 
Ran 1 test in 6.755s
FAILED (failures=1)
```

The control changes only its disposable copy. Historical reconstruction evidence
remains byte-for-byte unchanged. Browser coverage is limited to desktop Chromium;
other browsers, mobile layouts and a full accessibility audit remain outside
this suite.

## Other verification

The following checks completed locally before commit:

| Check | Outcome |
| --- | --- |
| `python3 -m unittest discover -s tests -v` | 16 tests passed (3.023 seconds) |
| `python3 -m phoenix verify` | Reference HTTP acceptance: 19/19 passed |
| `python3 -m phoenix audit` | Evidence audit passed |
| `python3 -m phoenix replay 20261022T014405Z-195739` | Archived candidate: 19/19 passed |
| `python3 -m compileall -q phoenix examples/reference tests/browser_smoke.py` | Exit 0 |
| `node --check docs/app.js` | Exit 0 |

CI continues to check the backend on Python 3.9, 3.12 and 3.13. Browser CI uses
Python 3.12 and the pinned optional test requirements. Pages performs the same
browser command through its existing publication workflow. Browser installation
requires network access; failed installation or tests fail the workflow instead
of silently skipping verification.

## Today's bounded reconstruction

One normal cycle was run, bounded to two attempts with a 300-second limit each.
Attempt 1 passed, so no second attempt was made. The existing authenticated CLI
used its clean default configuration; model selection and account permissions
were not changed.

- Record: [`20261022T014405Z-195739`](../evidence/20261022T014405Z-195739/report.json), started at `2026-10-22T01:44:04.242211+00:00` (UTC).
- Result: 19/19 independent HTTP acceptance checks passed.
- Candidate SHA-256: `71aeb29b9af0ceff0876881e437da1fe7a1ae000d21d2594f546d1cce8379ff3`.
- Predecessor: `20261021T024534Z-55970b`, the latest successful AI candidate before this run.
- Predecessor SHA-256: `202394078f6f72cfb81f05218177da4c57255247ef7d24bb97e183c36b670c0f`.
- Continuity: the predecessor created an inbox and event through HTTP; the new
  candidate reopened that SQLite database, read the old event and preserved its
  idempotency state.
- Generation: codex-cli 0.154.0, 79.913 seconds, zero observed tool calls.
- Model identity and monetary cost remain unknown. The public report retains
  only usage actually returned by the CLI.

The backend contract, generation procedure and HTTP verifier were unchanged.
This iteration adds notebook verification and records a new real reconstruction.
