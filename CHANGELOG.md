# v0.3.6 — 22.11.2026

- Отдельный financial ledger: нормализованный контракт, журнал исходных событий и SHA-256, история схем, карантин, неизменяемые источники.
- Независимая сверка total/categories/postings; пять состояний UI; контроль повторов и пересечений отчётов.
- Историческая COGS, партии, возврат по исходной цене, полные сторно продажи/сбора. При неизвестной себестоимости вклад заблокирован.
- Экран «Сверка финансов», локальный импорт отчёта/эталона/партии, защищённый API, точные строковые суммы для браузера.
- Fixture-only ReportLab для новых WB/Ozon сценариев; реальные маршруты не расширены, схемы не выдуманы.
- Финансовый demo и examples, закреплённые зависимости, сеть технически блокируется при pytest.

## v0.3.3 — RU Readiness (18.11.2026)

- Russian-market launch gates and architecture design in `docs/ru/`.
- Local HTTP peer IP validation supplements Host header checks; no remote access even with a spoofed Host.
- App posture endpoint explicitly reports public SaaS readiness = false.
- `app.ru_guardrails` verifies local mode and categorically blocks public/production mode.
- 7 new regression tests; this release is still not a launched compliant SaaS.

# Changelog

## v0.3.2 — Security / Financial Audit / Action Tracker (2026-11-18)

**Security:** local scrypt login, HttpOnly SameSite session, CSRF, Origin/Host policies, scoped audit log, session logout, login throttling, file permissions, delete confirmation. Vault previous emergency stop retained; store validity rechecked on credential dispatch, secured SQLite deletion settings.

**Financial engine:** cross-import probable overlap blocks contribution, negative net quantities block result; dated cost revisions and no future lookahead; historical contribution never represented as saved profit. Rate spikes use revenue after returns. Evidence status and period-specific finding keys.

**Functionality:** two explicit user-assumption scenarios with deterministic ruble arithmetic and auditable assumptions; action tracker. Beginner / finance / growth UI unchanged as modes; added financial scenario, dated COGS, action pages.

**QA:** 61 automated tests and mock-API browser smoke at 2 screen widths, no JavaScript page errors on tested paths.

**Not ready:** real provider connectors, public SaaS, multiuser RBAC, actual recoveries, LLM agents, 100000 RUB guarantee.
