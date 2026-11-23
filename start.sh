#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
python3 -m app.ru_guardrails --profile local --host 127.0.0.1 >/dev/null
python3 -m uvicorn app.server:app --host 127.0.0.1 --port 8765
