#!/usr/bin/env bash
set -euo pipefail

export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"
export PYTHON_BIN="${PYTHON_BIN:-/Library/Frameworks/Python.framework/Versions/3.13/bin/python3}"

PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"

cd "$PROJECT_DIR"

echo "[$(date '+%Y-%m-%d %H:%M:%S')] Building GeoSports dashboard..."
./scripts/build_site.sh

git add docs/index.html docs/.nojekyll

if git diff --cached --quiet; then
  echo "[$(date '+%Y-%m-%d %H:%M:%S')] No dashboard changes to publish."
else
  git commit -m "Update GeoSports dashboard"
  git push
  echo "[$(date '+%Y-%m-%d %H:%M:%S')] Published GeoSports dashboard."
fi

echo "[$(date '+%Y-%m-%d %H:%M:%S')] Drafting GeoSports recap if due..."
if ./scripts/draft_recap.sh --if-due; then
  echo "[$(date '+%Y-%m-%d %H:%M:%S')] Recap draft step completed."
else
  echo "[$(date '+%Y-%m-%d %H:%M:%S')] Recap draft step failed; dashboard publish already completed." >&2
fi
