# Engineering agent instructions — AI Profit Office

- Follow `prompts/MASTER_PROMPT_LOCAL_MAC.md` for development and `prompts/MASTER_PROMPT_PRODUCTION_RU.md` for architecture; select environment explicitly.
- Current build is LOCAL ONLY. Never connect to marketplace APIs. All tests use fake keys and `httpx.MockTransport` or scripted responses.
- Keep release history, local login, vault, emergency stop and financial ledger. No destructive migrations.
- Open-source API sources as of 2026-11-18: see `docs/API_CONTRACT_MATRIX_2026-11-18.md`. Endpoint existence != tested live compatibility.
- Fail closed on unknown financial categories or schema. No claim that opportunity estimates are realized profit. No 100000 ₽ guaranteed savings.
- Any test report must state actual command executed and number of tests passed; mocked HTTP is not live integration.
- Code reviews: no secrets/PII in logs/fixtures/zip; strict tenant/store isolation; store-binding of token in every dispatch; no write endpoints; test kill-switch during retries and between pages.
- Before a future live test explicitly request user's consent and ensure safe, local-only read-only key entry. Never ask users to paste tokens into chat.

## v0.3.5 LIVE READ ONLY security invariants (18.11.2026)

- All marketplace networking is OFF by default. Never enable network or arm a permit in CI/tests/release build.
- `ReadOnlyHTTPTransport` must only be invoked by `LiveGateway` after environment flag + TTL/attempt permit, vault/store scope and durable quota checks.
- Only exact read-only API paths and pinned provider hosts. No redirects, no trust_env proxies, no relaxed TLS, no unbounded response bodies.
- Any stop/revoke/rotate/expiry update invalidates live permits; error 401/403 stops affected credential.
- Do not recognize net profits from staged API replies; unknown schemas halt and remain unrecognized. Repeat after stop/restart must not duplicate events.
- All tests use httpx.MockTransport and fake keys only. New routes require verified contracts and dedicated tests.

## v0.3.6 reconciliation invariants

- Finance accepts only aipo.normalized-finance/1; never feed provider staging directly into this ledger.
- ReportLab is fixture-only, explicitly marked aipo.report-lab/1, never expand live paths based on its schemas.
- Independent controls must be separately provided; category or posting mismatch blocks contribution even if the total matches.
- Unknown schema/type/new sealed-report rows/conflicting event IDs remain quarantined; no automatic clearing.
- Money in HTTP responses uses decimal kopek strings; do not convert to JS Number.
- Batch costs are immutable. Return cost follows original sale. No automatic FIFO or reuse of returned batch stock yet.
- Run python scripts/test_offline.py -q on changes, retain truthful mock/live/macOS boundaries.

## v0.3.7 security foundation

- Financial app remains single-user LOCAL ONLY. saas_lab is a separate synthetic-only lab, never an authorization wrapper for old financial routes.
- No real seller/PII/token imports into lab. No cloud KMS, public hosting or marketplace networking in this task.
- Lab worker has only synthetic_audit; fence leases and recheck current membership at commit.
- Backups restore to a new path only; revoke sessions/invites, disable delegates, pause jobs and stop tenants.
- PostgreSQL draft has ROLLBACK and is untested: never describe it as active RLS.
