#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

PYTHON_BIN="${PYTHON_BIN:-/Library/Frameworks/Python.framework/Versions/3.13/bin/python3}"

"$PYTHON_BIN" -m geosports build

mkdir -p docs
cp dist/dashboard.html docs/index.html
touch docs/.nojekyll

echo "Built GitHub Pages site at docs/index.html"
