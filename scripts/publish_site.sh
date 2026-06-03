#!/usr/bin/env bash
set -euo pipefail

export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"

PROJECT_DIR="/Users/markconnolly/Desktop/Mark Coding"

cd "$PROJECT_DIR"

echo "[$(date '+%Y-%m-%d %H:%M:%S')] Building GeoSports dashboard..."
./scripts/build_site.sh

git add docs/index.html docs/.nojekyll

if git diff --cached --quiet; then
  echo "[$(date '+%Y-%m-%d %H:%M:%S')] No dashboard changes to publish."
  exit 0
fi

git commit -m "Update GeoSports dashboard"
git push

echo "[$(date '+%Y-%m-%d %H:%M:%S')] Published GeoSports dashboard."
