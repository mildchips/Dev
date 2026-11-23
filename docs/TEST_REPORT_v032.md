# Проверка релиза v0.3.2 — 18.11.2026

- `python -m pytest -q` → **61 passed** в среде сборки.
- `python qa/browser_smoke.py` → desktop 1440×900 и mobile 390×844: открываются Overview, Scenario Compare, Action Plan, History COGS. Ошибок JavaScript по проверенным переходам не обнаружено.
- `node --check web/main.js` → без синтаксических ошибок.
- Финансовые regression cases: возврат, COGS с датой начала, неизвестная историческая COGS, перекрытие импортов, возвраты из прошлого периода, старые ревью при смене данных, отрицательный сценарий снижения рекламы, отсутствие автоматической «экономии» при статусе implemented.
- Security regressions: закрытый доступ без логина, CSRF, сторонний Origin, Host, session logout, reset passphrase, brute-force lock, access guard при stop vault, store CRUD confirmation, audit log.

**НЕ проводились:** реальные запросы WB/Ozon, load test на 1000 пользователей, браузерный HTTP end-to-end по настоящему серверу (запрет loopback-навигации внутри браузера в среде сборки), независимый пентест, production data reconciliation, правовая проверка рекламного УТП.

Browser smoke fixtures получены из backend через TestClient, но сами HTTP browser fetch перехватываются локальным мок-обработчиком. Приложенные screenshots относятся к синтетическому учебному магазину. Реальные финансовые сведения не использованы.
