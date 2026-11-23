# RF-first roadmap update (after 0.3.3)

## P0 — before opening to external users

| Sprint | Work | Acceptance |
|---|---|---|
| RF-01 | Personal data inventory, legal entity/roles, notice/agreements and data flows | External counsel/DPO review and data residency sign-off |
| RF-02 | Identity, stores and multi-tenant RLS | Test 100+ adversarial cross-tenant operations, all denied |
| RF-03 | Production vault/KMS, per-store API credentials and distributed stops | Concurrent stop/rotation tested under active queues |
| RF-04 | RU cloud private network, TLS, WAF, single region multi-AZ | Infra-as-code, independent health probes from РФ |
| RF-05 | Queue-based connectors and idempotent sync | Failure isolation with 3 WB/Ozon test stores, deterministic finance reconciliation |
| RF-06 | Logs, backups, deletion policies, incident response | Verified restore, RPO/RTO documented, auditability |
| RF-07 | RU AI inference and provider compliance | No PII or token exfiltration; agent prompts use verified evidence only |

## P1 — after Private Alpha

| Sprint | Work | Acceptance |
|---|---|---|
| RF-08 | Load tests for 20→100→300→1,000 accounts | Latency, DB growth, API rate limit and cost tracked per tenant |
| RF-09 | Second provider / multi-geography DR planning | Independent failure domain, measured drill |
| RF-10 | Recurring payment and receipt flow | Payment data minimized; legal/accounting sign-off |
| RF-11 | Vendor security reviews and annual pentest cadence | No critical/high unresolved vulnerabilities |

## Risk decisions

- **Never** ship API keys in LLM prompts, issue trackers, public screenshots or developer workstations abroad.
- Never move customer ledger to a foreign hosted AI by default; add legal review and explicit product mode before any foreign transfer.
- Never promise availability SLA unless confirmed in actual monitoring and contract.
- Keep a low-cost single-operator local mode for internal tests and a distinct production implementation; no direct mode toggle from SQLite local to public SaaS.
