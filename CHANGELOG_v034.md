# Changelog v0.3.4 — Offline Connectors & Prompt Packs

Date: 2026-11-18. Base: v0.3.3 Russia-first local safety.

- Introduced `app/offline_sync.py`: Ozon `accrual/by-day` cursor, WB `sales-reports/detailed` 204/rrdId, fail-closed validation, decimal amounts, idempotent staging and checkpoints, bounded retries, hierarchical token stop and store binding.
- Introduced `app/httpx_mock_connector.py`: HTTP serialization through `httpx.MockTransport` only, with auth headers and SSL URL shape but no network use.
- Added virtual quota pacing for offline checks; not a distributed live quota implementation.
- Added `app.offline_demo` with 2 synthetic WB and 1 synthetic Ozon credentials; no user tokens.
- Added 43 tests; all **111 pytest tests** pass in the build environment; `compileall` and local smoke pass.
- Added two comprehensive copy-ready prompts for local Mac build and Russian production SaaS.
- Added API contract matrix and next implementation stage.

**Not implemented:** real API network connector, actual key validation, 1000-concurrent-user load testing, multi-tenant public SaaS, profit calculations from mock API events, production KMS, Russian legal compliance certification.
