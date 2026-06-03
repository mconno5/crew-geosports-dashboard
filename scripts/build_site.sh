#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

python3 -m geosports build

mkdir -p docs
cp dist/dashboard.html docs/index.html
touch docs/.nojekyll

echo "Built GitHub Pages site at docs/index.html"
