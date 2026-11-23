# Release sequencing — 3 real seller credentials → 1,000 subscriber SaaS

| Gate | Credential count | Scope | Before moving on |
|---|---:|---|---|
| v0.3.1 (в комплекте) | 3+ локально | Encrypted vault + stop flags + offline policy tests. No live calls. | `pytest`, visible stop audit, file modes 0600. |
| v0.3.2 | 3 пилотных | 1 проверенный READ ONLY финансовый API endpoint WB и один Ozon. | Vendor quotas, endpoint schemas, 401/403/429/5xx, source event ID. Stop confirmed against mocked transport before real access. |
| v0.3.3 | 3–20 | Sync cursor, catch-up scheduler, independent reconciliation against uploaded reports. | No duplicate amounts, recovery after failure, cost revisions. |
| v0.4 | 20–100 | Verified Evidence Packs, AI explanation only over checked ledger, job monitoring. | Proven false-positive review and data permission flow. |
| v0.5 Private Alpha | 100 | OIDC login/MFA admins, tenant-scoped RBAC, PostgreSQL, managed secrets/KMS, self-service consent. | Cross-tenant access tests, key rotation, incident runbooks, restore drill. |
| v0.8–0.9 | 300 | Real queue, rate limits per provider/endpoint, HPA, cloud cost telemetry, partner workspace. | 95% scheduled sync within window; security review. |
| v1.0 | 1,000 | Public billing, product economics, automation, alerting, KMS, multi-AZ backups. | Load tests with mocked WB/Ozon, 0 exposed secrets, cost and retention gates. |

**Before live use of the three keys:** user confirms data permissions, scopes and revocation access. Never put the real keys into chat, commit history, slides or prompts. Real API requests not enabled in this release.
