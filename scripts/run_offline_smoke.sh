#!/bin/sh
set -eu
cd "$(dirname "$0")/.."
python -m compileall -q app
python scripts/test_offline.py -q
python -m app.offline_demo
python -m app.finance_cli
printf '\nAll commands ran with synthetic accounts only. NO REAL API REQUESTS.\n'
