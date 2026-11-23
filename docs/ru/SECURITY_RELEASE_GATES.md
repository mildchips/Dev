# Production Security Gates — block deployment until verified

| Priority | Gate | Acceptance evidence | Status v0.3.3 |
|---|---|---|---|
| P0 | Российский оператор и юридические документы | Подписаны применимые договоры/уведомления/политики; DPO sign-off | OPEN |
| P0 | Хранение и обработка в РФ | Cloud configuration & data-flow inventory, foreign egress denied | OPEN |
| P0 | SaaS identities, MFA admins, owner/manager/auditor roles | E2E negative tests & pentest | OPEN |
| P0 | Tenant isolation | RLS, all DB access scoped, cross-tenant IDOR regressions | OPEN |
| P0 | Public TLS + WAF + rate limiter | Validated production ingress & probes from РФ | OPEN |
| P0 | Cloud KMS & marketplace credential controls | Rotation, limited permissions, emergency stop across workers | OPEN |
| P0 | Russian-only AI/telemetry path or lawful isolated alternative | Data-flow review, outbound deny tests | OPEN |
| P0 | Backups + restoration | DR exercise demonstrates RPO/RTO | OPEN |
| P0 | Delete/export/retention requests | Integration tests against every store, logs and backups | OPEN |
| P1 | HA multi-AZ | Fault injection, 99.9% target instrumentation | OPEN |
| P1 | SAST, SCA, dependency pinning, pen test | Signed report, no critical/high unresolved | OPEN |
| P1 | Marketplace API connectors | Tested throttling, invalid auth, token rotation | OPEN |
| P1 | Accurate financial data | Reconciled statements; missing data blocks profit claims | PARTIAL (normalized samples only) |
| P1 | Local research-only posture | Host & peer loopback, one passphrase, encrypted local vault | DONE (automated tests) |

## Why blocking is intentional

The product is still a one-operator **local** system using SQLite. Publishing it to `0.0.0.0`, a LAN address, a tunnel, load balancer or reverse proxy is prohibited. `python -m app.ru_guardrails --profile production` always fails closed in this version even with all `RU` region labels.

`app.ru_guardrails` is a check for gross unsafe configuration, **not** an attestation, security assessment, or legal compliance certification.
