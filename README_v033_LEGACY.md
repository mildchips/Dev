# ВАЖНО — Russia-first safety patch v0.3.3

Эта версия остаётся **локальной исследовательской Pre-Alpha**. Серверы в РФ не развёрнуты, соответствие 152-ФЗ не подтверждено, public SaaS запуск запрещён.

**Новые материалы:** `docs/ru/ARCHITECTURE_RF.md`, `LAW_152_FZ_CHECKLIST.md`, `OPS_SLO_DR.md`, `SECURITY_RELEASE_GATES.md`, `SPRINT_BACKLOG_RF.md`.

**Выполнено:** дополнительный сетевой localhost guard; отказ от профиля production; защищённая страница `/api/system/posture` с честным статусом готовности; 7 новых тестов. Запуск остаётся `./start.sh` и доступен только с данного компьютера.

Проверка профиля:

```bash
python -m app.ru_guardrails --profile local --host 127.0.0.1
python -m app.ru_guardrails --profile production  # ожидаемый отказ!
```

**Не отправляйте в чат/ИИ реальные API-ключи WB/Ozon или персональные данные ваших клиентов.**

---

# AI Profit Office v0.3.2 — Security & Profit Methodology

**Local research build**, not a public SaaS. Next release after v0.3.1. Includes local security hardening, deterministic audit engine v0.3.2, dated COGS, a two-scenario profit calculator and implementation tracker. No real WB/Ozon API calls or LLM calls are performed.

## What's new

- **Local login** required before any finance API, scrypt password verifier, HttpOnly session, CSRF protection, session/logout, login throttling.
- **Emergency token vault** from v0.3.1 preserved: one Ozon / multiple WB accounts, encrypted local secret store, hierarchical stop / rotate / revoke. Not accessible from web frontend.
- **Better financial integrity**: cross-file operation overlap detection blocks contribution, sale/return mismatches, dated cost schedule, warnings for static cost assumptions.
- **Transparent opportunity logic**: historical negative contribution != savings; suspected abnormal spend != recovered money; confirmed incremental profit is null until separately proved.
- **Scenarios**: user enters baseline and alternative volumes, price, commission, logistics, COGS and advertising. Returns hypothetical delta only, no prediction.
- **Action plan**: track suggested changes as draft / planned / implemented / rejected without crediting unmeasured gains.
- **UX**: beginner/finance/growth modes kept; added scenario form, dated COGS, action tracking, login/logout; demo shop only uses synthetic data.
- **Local audit trail** for updates, imports, action statuses and deletion with name confirmation.

## Install on macOS / Linux

Python 3.10+; tested here on Python 3.13. Do not upload real API keys to ChatGPT, code, CLI arguments, log files, screenshots or browser forms.

```bash
unzip AI_Profit_Office_v0.3.2_Audit_Hardened.zip
cd AI_Profit_Office_v0.3.2_Audit_Hardened
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python -m app.local_auth init-password
./start.sh
```

Open **http://127.0.0.1:8765**. Set a unique 12+ character passphrase in the terminal (hidden input), then sign in via the browser. Existing `~/.ai_profit_office/prealpha.db` is migrated by non-destructive `CREATE TABLE IF NOT EXISTS` additions. **Back up existing finance data first**; for migration from an unknown older schema test with a database copy. Old passphrase can be reset locally with `python -m app.local_auth reset-password` (invalidates sessions). 

If port `8765` is busy, stop the older local app first. Server must remain bound to `127.0.0.1` (this is the default in `start.sh`). Do **not** expose this app through ngrok, tunnels, reverse proxy, public cloud or router port-forwarding.

### Three existing marketplace cabinets

After login, create **three distinct non-demo stores** for WB #1, WB #2 and Ozon #1. Do not mix platforms in one store. Then run:

```bash
python -m app.integration_cli init-key
python -m app.integration_cli stores
python -m app.integration_cli add --store WB_STORE_1 --provider WB --label wb-1
python -m app.integration_cli add --store WB_STORE_2 --provider WB --label wb-2
python -m app.integration_cli add --store OZON_STORE --provider Ozon --label ozon-1
python -m app.integration_cli status
```

**Keys are entered only via hidden CLI prompts.** Vault initialization is a one-time operation; if `init-key` says the key exists, do not overwrite it. For Ozon, Client-Id is requested. These credentials are **not validated** and there is **no live API sync** in this version.

Emergency stop:

```bash
./scripts/kill-all.sh
python -m app.integration_cli status
# Only after investigation:
python -m app.integration_cli resume --scope global --id '*'
```

For a possibly leaked key also revoke it at WB/Ozon immediately; application stop is not provider-side revocation. See `docs/SECURITY_RUNBOOK.md`.

### Financial analysis workflow

1. Choose a store and upload the **normalized CSV/XLSX** (not an unmodified WB/Ozon financial export). Template: `samples/normalized_sample.csv`.
2. Enter cost per SKU. For accurate historical analysis, use **Себестоимость → Историческая себестоимость** with effective dates. Static cost is an approximation.
3. Inspect **Возможности** for financial anomalies; note negative SKU exposure is *not* guaranteed savings.
4. In **Сценарии прибыли**, enter *both* baseline and alternative assumptions. The system calculates conditional delta. No demand elasticity is inferred automatically.
5. In **План действий**, track what you plan to change; `implemented` is not a verified financial improvement.
6. Use `GET /api/local/audit` (local auth required) for basic change history.

The fixed contract is **money values in kopeks internally**, sales/returns signed, expenses signed, COGS from quantities and dated costs, and never combine payment transfers with revenue.

## Test

```bash
python -m pip install -r requirements-dev.txt
python -m pytest -q
# Optional frontend mock-render (Chromium + Playwright required):
python qa/browser_smoke.py
```

Regression tests: local auth/security, vault kill-switch, normalized file parsing, financial math/returns, cost revisions, overlapping imports, positive and negative counterfactual scenarios, manual action status. Browser QA uses mocked fetch with actual API-produced fixtures because localhost navigation from browser is blocked in the test container; it is **not** an HTTP end-to-end test.

## Reports

- `docs/FINANCIAL_MODEL_AUDIT.md` — formal model, proven failure modes, formula, audit gate for 100 000 ₽ USP.
- `docs/SECURITY_AUDIT_v032.md` — fixed vulnerabilities vs production gaps.
- `docs/IMPLEMENTATION_STATUS.md` — actual implemented versus planned.
- `docs/NEXT_SPRINT_v033.md` — steps toward first read-only WB/Ozon integration and multiuser readiness.
- `docs/ARCHITECTURE_1000.md` — previous capacity modeling (scenario only, not load-tested).

**Not available yet:** live WB/Ozon connectors, historical purchase batches and true order-level return matching, independent financial reconciliation, causal measurement of improvements, AI/LLM agents, multi-tenant roles, PostgreSQL/KMS production infrastructure, payments and public access.
