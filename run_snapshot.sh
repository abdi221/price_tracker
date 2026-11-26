#!/bin/bash
set -u 
cd "$(dirname "$0")"

echo "=== $(date '+%Y-%m-%d %H:%M') ==="

git pull --rebase --autostash --quiet || echo "Warn: git pull failed, continuing with local basket"

.venv/bin/python -m pricewatch.snapshot basket.csv --out data


git add data/
git commit --quiet -m "local snapshot $(date '+%Y-%m-%d %H:%M')" || echo "nothing to commit"
git push --quiet || echo "WARN: git push failed: data is saved locally, push it by hand later"
