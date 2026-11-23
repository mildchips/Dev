# AI Profit Office v0.3.4 — mock-first integration harness

## What's included

- Full previous v0.3.3 local application (`app.server`, local password, financial UI, protected vault/kill switch).
- New `app.offline_sync`: isolated Ozon `by-day` and WB finance detailed simulated pagination, response contract validation, idempotent checkpoints, per-store source event separation, token/store binding, retry/stop enforcement.
- New `app.httpx_mock_connector`: actual HTTP request/header serialization through **httpx.MockTransport** only. No live marketplace calls.
- `app.offline_demo`: stand-alone temp DB with **three artificial stores**, synthetic keys and no input tokens.
- Synthetic 1000-tenant staging smoke test, not production benchmarking.
- Two long, implementation-ready master prompts for Mac/local and Russia-first production.

## Run without tokens

```bash
unzip AI_Profit_Office_v0.3.4_Offline_Contracts.zip
cd AI_Profit_Office_v0.3.4_Offline_Contracts
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python -m pip install -r requirements-dev.txt
./scripts/run_offline_smoke.sh
```

Expected: all tests green, `OFFLINE MOCK ONLY`, `outbound_http_calls: 0`, three fake stores, emergency stop PASS.

To start the existing local UI (not connected to API):

```bash
python -m app.local_auth init-password
./start.sh
# open http://127.0.0.1:8765
```

The app still has no production networking backend, and test events are not loaded into finance dashboard. Never use it as public web SaaS or promise that adding real keys will produce data: **live connector implementation and first live validation are a separate guarded sprint**.

## Prompts

- `prompts/MASTER_PROMPT_LOCAL_MAC.md`: complete local build, integration, security and test procedure.
- `prompts/MASTER_PROMPT_PRODUCTION_RU.md`: Russian SaaS plan, 1000 users, RBAC/RLS/KMS, resilience, laws, CI/CD and rollout.

## Important docs

- `docs/API_CONTRACT_MATRIX_2026-11-18.md`: actual 2026 API endpoints, restrictions and verification status.
- `reports/TEST_REPORT_v034.md`: what was really tested; what remains unverified.
- `docs/NEXT_STEPS_v035.md`: next sprint and manual future live key test conditions.

**No user secrets are included. Keep real tokens in local encrypted vault only.**
