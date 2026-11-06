# Contributing

Small, focused improvements are welcome.

- Open an issue first for anything larger than a typo or a clearer example, so the scope can be agreed before work starts.
- Never weaken the safety rules: drift must stay a fail, and a report must name the real package, file, and line.
- Every claim in a proposed change must be verifiable: a command must be runnable, a reported import must be real, a badge must be true.
- Run `python3 tests/test_import_sync.py` before opening a pull request. It must pass.
- Do not add placeholder text, invented features, or copied content without attribution.
- Python standard library only — no new dependency without a reason in the issue.
