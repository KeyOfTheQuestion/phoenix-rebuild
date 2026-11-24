# Run delivery verification outside the checkout · 2026-11-24

Invoking `tests/delivery_smoke.py` by absolute path from another directory
exposed a gap in the verification tooling. The runner itself locates its
checkout, but the H22 history CLI and J04 interrupted-delivery CLI inherited
the caller's directory. Their `python -m phoenix` children could not find
the uninstalled package. Earlier CI runs invoked the runner from the checkout
root and did not cover this case.

H22 was first reproduced from a temporary directory: the CLI exited 1 with
`No module named phoenix`. Its original local failure log is retained with
SHA-256 `cb4eadda85243dcd31fe5c6ed52f0ed15566fef53eb7d7ef50efaee908b9680e`.
After its two subprocess invocations selected the checkout root, the focused
check passed. A complete smoke from outside the checkout then passed 55/56:
J04's separate CLI had the same import problem and never reached its receiver.
That intermediate failed observation and its matching sources are preserved:
[55/56 report](2026-11-24-delivery-cwd-failure/python39.json),
[source manifest](2026-11-24-delivery-cwd-failure/manifest.json).

J04's process now also selects the checkout root. Its focused external-directory
check passed, including an actual receiver request, process termination and
retention of the unconfirmed journal row. No parent process changes its global
working directory, and no package installation or `PYTHONPATH` change is needed.
The delivery client and backend contracts retain their previous behavior;
all 56 client checks retain their previous assertions. This repairs test
invocation, not a backend verdict.

CI's Python matrix and the existing Pages workflow now invoke the delivery
smoke by absolute path with `working-directory: runner.temp`. The Pages guard
remains before publication. No checks are skipped or counted as additional
AI reconstruction items. Frozen client reports describe checkout observations;
their source snapshots are not a standalone installed Python package.

After both fixes, all 167 unit tests and 19 independent reference HTTP checks
passed on Python 3.9, and the evidence audit passed. The complete delivery
smoke invoked by absolute path from outside the checkout then passed 56/56
on CPython 3.9.6 / SQLite 3.43.2 using today's actual AI inbox.
[Successful external-directory report](2026-11-24-delivery-sources/python39.json),
[source manifest](2026-11-24-delivery-sources/manifest.json).

Today's single bounded contract-002 reconstruction used the existing
authenticated Codex CLI 0.166.0 with its clean default configuration and passed
32/32 on its first attempt. Runtime was CPython 3.9.6 / SQLite 3.43.2, and
`20261103T023452Z-4ec8a9` supplied the required v1 migration fixture. The CLI
reported 115.51 generation seconds, 24,834 input tokens, 4,594 output tokens
and 347 reasoning output tokens. The resolved model and monetary cost are
unknown. [AI reconstruction report](../evidence/20261124T023750Z-d49f17/report.json).

The separate v2 handoff from the November 23 candidate to today's candidate
passed 8/8 using HTTP-created data and disclosed synthetic timestamp aging.
This bundle remains outside the AI ledger and 32-check verdict.
[Continuity report](2026-11-24-continuity/report.json).

## Publication failure and bounded recovery

The initial [Pages run](https://github.com/KeyOfTheQuestion/phoenix-rebuild/actions/runs/45314396645/attempts/1)
passed all verification and uploaded its artifact, then deployment reported
zero matching artifacts. A later metadata query found the uploaded artifact.
Rerunning that job uploaded another `github-pages` artifact, and deployment
then rejected two matching names. A [fresh workflow run](https://github.com/KeyOfTheQuestion/phoenix-rebuild/actions/runs/45315311745)
again passed verification but reported zero artifacts at deployment. These
publication failures do not alter any AI or client acceptance observation.

The existing workflow now names the artifact using the workflow run and attempt
IDs. If deployment fails, it waits ten seconds and makes one deployment-only
retry against that same artifact, without uploading again or rerunning tests.
The second failure still fails the job. This is bounded recovery for the
observed artifact-discovery problem, not a guarantee about GitHub availability.
Action revisions, verification guards, workflow permissions and credentials
are unchanged. The environment URL accepts either successful deployment's output.
