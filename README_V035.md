# AI Profit Office v0.3.5 — READ ONLY Connector / LOCAL

Релиз содержит приложение из v0.3.4 и дополнительный защищённый `LiveGateway`. **Все реальные соединения с Ozon и WB по умолчанию выключены.**

## Установка на Mac

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python -m pip install -r requirements-dev.txt
python -m pytest -q
./scripts/run_offline_smoke.sh
```

Создание локального пользователя и интерфейса — по [`README.md`](README.md), с включённой локальной авторизацией.

## Офлайн-диагностика ключей (без сетевых обращений)

```bash
python -m app.integration_cli status
python -m app.live_cli status
python -m app.live_cli expiries
python -m app.live_cli preflight --credential CRED_UUID --store STORE_UUID --provider WB
# Для Ozon: --provider Ozon; предварительно установить дату окончания ключа.
```

Не присылайте ключи в переписку. Для Ozon реальный срок введён вручную по личному кабинету: `python -m app.live_cli set-expiry --credential CRED_UUID --date YYYY-MM-DD`.

## Live (не запускать до отдельного решения)

Всё описано в [`docs/LIVE_RUNBOOK_v035.md`](docs/LIVE_RUNBOOK_v035.md). Автоматические тесты никогда не включают настоящий HTTP к маркетплейсам.

## Документы

- [`docs/RELEASE_NOTES_v035.md`](docs/RELEASE_NOTES_v035.md) — изменения
- [`docs/CONTRACT_GAPS_v035.md`](docs/CONTRACT_GAPS_v035.md) — непроверенные финансовые контракты и последние изменения Ozon
- [`docs/LIVE_RUNBOOK_v035.md`](docs/LIVE_RUNBOOK_v035.md) — ручной будущий canary
- [`reports/TEST_REPORT_v035.md`](reports/TEST_REPORT_v035.md) — отчёт QA
- [`docs/NEXT_SPRINT_v036.md`](docs/NEXT_SPRINT_v036.md) — следующие действия

**Нельзя использовать этот локальный SQLite-релиз как многопользовательский публичный SaaS.**
