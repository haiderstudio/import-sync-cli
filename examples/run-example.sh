#!/bin/sh
# Run the checker on the bundled example projects.
# Run from the repo root: sh examples/run-example.sh
# The sample project has drift on purpose (one missing, one unused),
# so its check exits 1 — that is the expected result here.
# The clean project has no drift and exits 0.
set -e
cd "$(dirname "$0")/.."
echo "--- sample project (drift on purpose) ---"
python3 src/import_sync.py examples/sample-project || true
echo "--- clean project (no drift) ---"
python3 src/import_sync.py examples/clean-project
