# Операции, доступность и аварийное восстановление

**Только проектный runbook.** Показатели не измерены и не обещаны клиентам.

## Мониторинг: четыре независимые панели

1. **Web service:** availability from real Russian probes, 5xx, latency P50/P95, TLS expiry, CPU/RAM, error budget.
2. **Marketplace connectors:** WB/Ozon API success/401/403/429/5xx, sync lag by store, latest settled statement, token age, retries and dead-letter tasks.
3. **Data safety:** missing event IDs, reconciliation mismatch, missing cost revisions, staleness, financial amount blocked, tenant access denials.
4. **Security:** login anomalies, invite/revoke events, KMS uses, emergency stop transitions, suspicious exports, high-volume reads, rejected foreign data egress.

## SLO / policy

| Indicator | Preliminary objective | Escalation |
|---|---|---|
| Russian UI availability | 99.9% / 30 days | 5 min sustained unavailability |
| API p95 warmed response | <500 ms | >1s for 10 min |
| Data sync lag | Platform/endpoint-defined; show last sync always | Exceeds display threshold per endpoint |
| RPO | ≤15 min | DR drill failure |
| RTO | ≤60 min | DR drill failure |
| Cross-tenant unauthorized access | 0 | Sev-1 immediately |
| Credential leakage | 0 | Sev-1 + platform revocation |

## Incident playbooks

- **WB/Ozon rate limiting:** stop retries storm; respect vendor Retry-After; protect other tenants via per-credential token bucket; mark feed stale.
- **Cloud zone failure:** LB routes to healthy app node; database HA failover; user may see temporary read-only status; monitor until recovered.
- **Whole region outage:** do not claim automatic recovery until provider-independent drills prove it. Warm/standby plan in separate Russian geography, rebuild from encrypted snapshots and WAL/PITR.
- **Lost/stolen integration token:** global/provider/store/credential emergency stop, revoke at marketplace, rotate credential, investigate use, avoid logging secret.
- **Suspected personal-data incident:** isolate, preserve evidence securely, activate legal/DPO notification process, do not wait for complete forensic conclusion before legal deadlines.
- **Financial data mismatch:** block opportunity totals affected by incomplete source; continue access to last trusted statement with stale/disputed label.

## Backup discipline

PITR and encrypted backups in РФ, access separate from app service account, restore test on a schedule, key backup recovery, cross-zone copies, retention and deletion mapping. A backup *exists* is not proof of recoverability. Retention must respect validated legal obligations.

## Required drills before 100 users

- Kill one worker while importing; ensure task idempotency.
- Drop WB API connectivity for 20 min; UI stays online with stale flag.
- Kill primary DB host; verify HA and connection resilience.
- Restore last encrypted backup; measure RPO/RTO against agreed goals.
- Revoke manager from store while report is being generated; confirm no export after revoke.
- Rotate WB/Ozon secrets while jobs are queued; ensure stale secrets cannot be dispatched.
- Supply forged financial exports and prompt-injection strings; no token exfiltration or unsupported money claims.
