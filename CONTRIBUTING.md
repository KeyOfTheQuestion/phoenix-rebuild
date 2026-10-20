# Contributing

Start with an issue describing a specific observable requirement. Include a
request/response or data-continuity example, and state what should remain
compatible. The smallest useful contribution is a well-defined new contract
scenario, a reproduced harness bug, or an independently rerun candidate.

Use Python 3.9+ and the standard library for the initial subject and harness.
Avoid new dependencies unless they solve a concrete requirement. Keep generated
implementations, reference code and the evaluator distinct. Changes to a contract
must update its version and explain the compatibility consequence.

Before a pull request:

```sh
python3 -m unittest discover -s tests -v
python3 -m phoenix verify
python3 -m phoenix audit
```

Report actual results and disclose human edits. Do not overwrite historical
failures. Do not submit production payloads, API keys, customer records or raw
account logs. For suspected security issues, use GitHub private vulnerability
reporting if available; otherwise open an issue with impact and contact intent
only, without credentials or a live exploit target.
