# AI Profit Office v0.3.4 — offline validation (18.11.2026)

## Actual test evidence

- `python -m pytest -q` — **111 passed** (Python 3.13, Linux container).
- `python -m app.offline_demo` — simulated **WB #1, WB #2, Ozon #1**, no outbound; two-page Ozon, WB HTTP 204 end; emergency global stop => PASS.
- `python -m compileall -q app` — PASS.
- Synthetic 1000-tenant staging test: 1000 unique stored events and independent counters. **Not** 1000 concurrent users, **not** production performance, **not** a cloud HA test.
- HTTP wire construction tested through `httpx.MockTransport`: WB Authorization, Ozon Client-Id/Api-Key, endpoint paths, JSON bodies, error and redaction; not live traffic.

## Coverage categories

| Test family | Coverage | Result |
|---|---|---|
| Existing application | 68 inherited tests (local auth, financial math, vault, imports, Russia guardrails etc.) | PASS |
| Offline connector staging | 36 new tests (Ozon/WB cursor & schema, stop, 429/503, restart, 1000 tenant smoke, isolation) | PASS |
| Mock HTTP wire | 7 new tests | PASS |
| Total | **111 tests** | **PASS** |

## Security evidence

- Only fixture keys `SYNTH-*`/`DUMMY_TEST_NOT_REAL`; no tokens obtained from user.
- Real network transport cannot be passed into offline `Gateway`; `HttpxMockTransport` always installs `httpx.MockTransport`.
- Vault credential is checked against both `platform` **and** `store_id` before mock dispatch.
- Cross-store access prevented at sync request; no secret leakage in test staging or error text.
- Stop is checked before each attempt; stops simulated between pages and before retry.
- Unknown schema and cursor loops abort; they do not commit false financial totals.

## Known gaps / not tested

- No real WB/Ozon key scopes, provider 401/403 behavior, live endpoint schema, actual rate quotas, response latency, network/TLS routing, account permissions or real marketplace financial reconciliation.
- No production KMS/RLS/Redis/HA/load testing. App remains local-only with SQLite and single-user auth.
- Staged mock events are **not** integrated into financial ledger; current mock cannot validate calculated profits.
- Ozon `accrual/types`, `accrual/postings`, OAuth/Device Code and WB `sales-reports/list` not implemented.
- 152-ФЗ compliance and security certification not verified. Need Russian legal and security review.
- No macOS-specific runtime verification during this build; local Mac is the deployment target.

**GO for local synthetic testing. NO-GO for public SaaS and unreviewed real-token use.**
