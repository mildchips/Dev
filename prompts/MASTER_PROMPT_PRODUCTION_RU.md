# MASTER PROMPT 02 — AI Profit Office: production SaaS для рынка РФ (1000+ пользователей)

**Роль:** Principal Solution Architect + SRE Lead + Security Architect + Staff Data Engineer + FinTech-grade Financial Systems Architect + Head of Product + QA Automation Lead. Исполняй задачу как инженерную программу: проектируй, создавай IaC, код, CI/CD, тесты, миграции, runbooks, планы эксплуатации и результаты нагрузочного тестирования. Не подменяй сделанную работу красивыми диаграммами.

## 0. Контекст проекта и конечный продукт

AI Profit Office — российский B2B SaaS для микроселлеров Wildberries и Ozon, который находит проверяемые финансовые потери, оценивает резервы повышения прибыли, формирует понятные действия и контролирует эффект. Тарифы 990 / 1990 / 2990 ₽/мес., три режима UX «Начинаю / Контролирую / Развиваю», бесплатный Manager Workspace с правами владельца магазина. Выигрываем не «подсчётом чистой прибыли», а доказательным циклом `Find -> Verify -> Estimate -> Act -> Measure`. Формулировка «Найдём от 100 000 ₽» — маркетинговая гипотеза, юридическая и продуктовая проверка обязательна.

Базовый код — текущий локальный AI Profit Office с проверенными offline adapters. Не переносить pre-alpha SQLite/локальный ключ прямо в интернет. Требуется отдельная production-ветка и gated release. Цель v1: 1000 платящих пользователей, модель 1.4 кабинета на пользователя (1400 кабинетов), дальнейший рост до 10k.

## 1. Непреложные правила

1. Вся основная обработка финансов и персональных данных российских пользователей, первоначальные базы, журналы, ключи, backup, системы мониторинга и обработка AI — в согласованном российском контуре. До юридического заключения запрещать передачу ПД за пределы РФ и удалённый доступ с неразрешённых локаций к чувствительным данным.
2. Законность не следует автоматически из российского хостинга: 152-ФЗ, 149-ФЗ, договоры, уведомление РКН, политика обработки ПД, поручения обработчикам, оценка угроз, категория ИСПДн, меры защиты, требования по утечкам; актуальную редакцию перепроверить с юристом/ответственным по ПД до Go-Live. Не объявлять сертификат/соответствие без аудита.
3. Ни один секрет не попадает в env в открытом виде при деплое, git, CI logs, APM, chat, telemetry; необходим KMS + versioned envelope encryption, RBAC/MFA, rotation, revocation.
4. Принцип отказа: неизвестные финансовые поля/потеря данных/незавершённая синхронизация => `UNVERIFIED`, финансовый результат не публикуется.
5. Read-only внешние токены и API; запрещены write endpoint-ы для цен, рекламы, остатков, заказов и денег в v1.0.
6. Не смешивать операционную прибыль/маржу, cash flow, комиссию, выплаты и резервы оптимизации.
7. Каждый компонент инфраструктуры должен иметь заявленные и проверенные пределы нагрузки, стоимость, failover, runbook и собственника.
8. Доступность 99.9%, целевые RPO <= 15 мин, RTO <= 60 мин — первоначальные SLO, проверять учениями, не обещать как достигнутые до испытаний.

## 2. Discovery и отдельные обязательные документы

1. Инвентаризация всех типов данных: идентификатор селлера, ФИО/почта, контакт, кабинет, токены, SKU, цены, закупки, сведения покупателя, суммы, чеки, рекламные операции, скриншоты, журналы. Классификация ПД/коммерческая тайна/операционные данные.
2. Data flows: какие системы, где физически находятся серверы, кто оператор/обработчик, какие подрядчики/субпроцессоры, трансграничные потоки и TTL хранения.
3. DPIA/модель угроз/матрица активов/угроз/мер, оценка применимых ПД требований и рекомендуемые решения; до запуска — экспертная правовая проверка.
4. Документы: ADR, C4 (context/container/component), ERD, Event Catalog, API Specification, Disaster Recovery Plan, Incident Response Plan, Security Policy, Cost Model, SLO/SLI, Runbook, Product Requirements, backlog.
5. Проверить доступность российских провайдеров и стоимость (Yandex Cloud / Selectel / VK Cloud как кандидаты) по актуальным тарифам: не придумывать цены. Избегать единой точки отказа.

## 3. Целевая инфраструктура

1. Российский DNS/CDN/защита от DDoS, HTTPS TLS 1.2+/1.3, TLS termination, WAF, secrets redaction; резервная схема для DNS/доступности из РФ.
2. Два+ экземпляра stateless FastAPI веб-приложения за балансировщиком в разных зонах доступности; health probes readiness/liveness; graceful shutdown; deploy без простоя.
3. Managed PostgreSQL в РФ с HA, автоматическими backup, WAL/PITR, шифрованием, отдельными секретами и failover rehearsal.
4. Redis/очередь задач с durable execution для синхронизаций, dead letter queue и scheduled retry; Redis — не источник финансовой истины.
5. Object Storage в РФ: private buckets, server-side encryption, TTL, backfill/versioning, ограниченные pre-signed URLs, lifecycle, без публичных ACL.
6. KMS/Secrets Manager в РФ: envelope encryption, key rotation, access by workload identity, audit of decryption, emergency-stop independent of web app.
7. Observability stack РФ: метрики, traces без PII, редактированные логи, правила алертов, on-call, retention.
8. Сеть: приватные subnet для БД/очередей/KMS, минимальные egress rules для WB/Ozon и одобренных AI сервисов, no SSH public, bastion/VPN/SSO/MFA, доступ разработчиков к реальным данным только по минимальным разрешениям.
9. Feature flags, canary deploy, автоматический rollback, несовместимые миграции запрещены без expand/contract.
10. Изолированные окружения `dev / staging / prod`, раздельные ключи, mock-only в dev, sanitized fixtures и обучающие данные в staging.
11. Отдельные IaC-модули для сетей, БД, object storage, KMS, секретов, контейнеров, очередей, мониторинга, backup, CI/CD. Никаких test secrets в state IaC.

## 4. Multi-tenancy и безопасность прав

1. Объекты: `organization/tenant`, `user`, `membership`, `store`, `credential`, `provider_account`, `job`, `event`, `report`, `evidence`, `action`, `billing`, `audit`.
2. Роли: `owner`, `manager`, `auditor`, `support_limited`, `system_worker`; право по умолчанию запрещено.
3. Каждый запрос авторизуется по user identity + membership + tenant_id/store_id; нельзя доверять store_id из browser query.
4. PostgreSQL RLS на tenant-scoped tables с строгой настройкой контекста транзакции; отдельный тест `cross_tenant_access` на SELECT/UPDATE/DELETE/FILE/EXPORT и фоновые задания.
5. Manager Workspace получает доступ только по инвайту собственника с областью действия и сроком; отзыв доступа немедленно закрывает UI, API, экспорт, ссылки и запланированные задачи.
6. MFA для администраторов, поддержки и операций с ключами, строгая политика сессий/CSRF, passwordless/SSO по возможности, ограничение попыток входа.
7. Шифрование PII/коммерческих данных at rest/in transit, защита от XSS/SSRF/CSRF/SQLi, safe file handling, MIME/type checks, лимиты размера и парсинга XLSX/CSV.
8. Защита от LLM prompt injection: входящие отчёты/комментарии считаются недоверенным содержимым; агенты не получают токены и не исполняют инструкции из данных.
9. DLP/log scanning на секреты, регулярный dependency scanning, контейнерный scanning, SBOM и подписанные образы.
10. Не использовать данные одного клиента для публичной демонстрации или обучения моделей без отдельного разрешения и правового основания.

## 5. Vault, ключи, авторизация площадок и аварийная остановка

1. Каждый credential криптографически привязан к конкретному tenant/store/provider, имеет версии, expiry, scope, status, audit.
2. Поддержать WB personal/service access tokens и Ozon Seller API Client-Id+Api-Key; проработать Ozon OAuth/Device Code (если доступно по условиям) как отдельный вариант с обновлением access/refresh по документированному контракту.
3. Трёхмесячное истечение новых Ozon Seller API ключей с сентября 2026: диагностика срока, предупреждения, `EXPIRED` без потери истории.
4. KMS envelope encryption: data key per tenant/credential, применение AAD tenant+store+version, key rotation без простоя и без сохранения plaintext в диске/логах.
5. Token access только у background connector workers; ни frontend, ни LLM, ни аналитические SQL функции не могут получить секрет.
6. Emergency stop: глобальный / WB / Ozon / tenant / store / credential / API-method. Проверять до каждого вызова, перед каждым retry, перед исполнением очередной задачи; единый kill state в сильно согласованном хранилище.
7. В-flight запрос нельзя гарантированно отозвать: при утечке дать процедуру provider-side revocation, принудительного logout, cache purge и расследования.
8. После stop queued jobs становятся `PAUSED_BY_OPERATOR`, не перераспределяются в обход блокировки, runbook resume с MFA/двойным подтверждением.
9. Для каждой ротации — idempotent version transition; revoked версии невосстановимы; emergency test 1x/месяц.

## 6. Marketplace API read-only и rate limit orchestration

1. На дату релиза сверить актуальные официальные OpenAPI/Release Notes, список endpoint, required roles, request/response, quota semantics, deprecation dates. Не копировать устаревшие методы из SDK без проверки.
2. Ozon 2026 финансы: `/v1/finance/accrual/by-day`, `/v1/finance/accrual/postings`, `/v1/finance/accrual/types`; последовательная обработка, by-day+last_id; типы начислений и версия маппинга; кодировать неклассифицированные как `UNMAPPED`.
3. WB 2026 финансы: `/api/finance/v1/sales-reports/list`, `/api/finance/v1/sales-reports/detailed`, `/api/finance/v1/sales-reports/detailed/{reportId}`; JSON camelCase, суммы могут быть decimal strings; 204 при завершении деталей; большой 64-bit rrdId.
4. Нельзя получать данные через скрапинг закрытого интерфейса или подменять пользовательские сессии в обход условий площадки.
5. Rate limiter распределённый per provider / tenant / credential / endpoint / account; honor Retry-After и дополнительные серверные rate headers, adaptive backoff, jitter, circuit breaker. Ручное изменение quotas только с подтверждённой документацией.
6. Очереди отделяют `initial_backfill`, `daily_sync`, `reconciliation`, `retry`, `manual_refresh`; приоритеты и квоты не должны приводить к starvation малых пользователей.
7. Idempotency keys и source event IDs; checkpoint/offset/last_id/rrdId только после успешной атомарной записи данных; повторный запрос не создаёт дублей.
8. Новые schema versions не должны молча превращать деньги в нули; schema drift alert + quarantine + compatibility tests.
9. 401/403/402: не spam/retry; переводить в `NEEDS_REAUTH/NO_SCOPE/PAID_ACCESS_REQUIRED`, owner получает понятное уведомление.
10. Истечение Ozon токена, частичный outage WB, таймауты, 429, cursor loop и несовпадение валидированной модели — обязательные инцидентные сценарии.
11. API-sync должен быть отделён от UI: сайт доступен при outage WB/Ozon; показывать `last_verified_at`, freshness и предупреждения.

## 7. Нормализованный финансовый контур

1. Event ledger append-only: raw source (защищённый), normalized entry, source_event_id, semantic type, operation date, report period, SKU, qty, signed amount, currency, schema version, ingestion timestamp, checksum, evidence URL/reference.
2. Расхождения между разными отчётами площадок должны разбираться явно; выплаты не считать выручкой, заказы не считать покупками, рекламную атрибуцию не считать причинным эффектом.
3. Поддержать комиссии, скидки, логистику, хранение, штрафы, возвраты, перерасчёты, закупочную себестоимость по партиям/датам, налоги только при корректных данных и настройках.
4. Финансовую математику выполнять в Decimal/целых копейках, не в LLM/JS float.
5. Opportunity Engine: `SIGNAL -> EVIDENCE -> VERIFIED_RISK -> SCENARIO_RANGE -> ACTION -> MEASURED_RESULT` с блокировками при incomplete data.
6. Отдельная causal evaluation: контрольные периоды, сезонность, изменение цен/ассортимента/рекламы; без причинной проверки нельзя выдавать hypothetical delta за реальные сохранённые деньги.
7. Не суммировать перекрывающиеся возможности; agent recommendations проходят conflict detection (снижение рекламы может сократить продажи и маржу).
8. Политики безопасности: AI только читает evidence, строит объяснение и сценарии, человек подтверждает внешние действия; не изменяет цены/ставки/банковские операции в первой публичной версии.

## 8. Product UX / pricing / PLG

1. Стартовый экран: найденные доказательные возможности + финансовые риски + три действия, не только P&L.
2. UX `Начинаю / Контролирую / Развиваю` независимо от тарифа; тарифы 990/1990/2990 ₽ по квотам ресурса и глубине аналитики.
3. Показывать отдельно `потенциал за месяц`, `проверенные начисления за период`, `в процессе`, `фактически измерено` — без агрессивного суммирования.
4. Onboarding Ozon/WB: выбор магазина, авторизация/инструкция получения прав, статус ключа, загрузка себестоимости, запуск первой безопасной сверки, first trusted value.
5. Информирование о stale financial data; доступ к historic reports при временной недоступности API.
6. Бесплатный Manager Workspace, инвайты и права; реферальная атрибуция по согласию, вознаграждение только за реальные оплаты, предотвратить self-referral/fraud.
7. Платёжный провайдер, чек/фискализация, возвраты, договор-оферта, биллинг и налоги — после юридической/бухгалтерской проверки требований для РФ.
8. Запланируй retention cohorts, onboarding funnel, CAC/ARPU/LTV, paid conversion, time-to-first-value и использование рекомендаций.

## 9. Расчёт ёмкости, производительности и бюджета

1. Базовый сценарий: 1000 пользователей × 1.4 магазина = 1400 кабинетов.
2. Для первоначальной верхнеуровневой модели: 12 групп данных × 3 API-запроса/группу/сутки -> около 50400 запросов/сутки или 1.512 млн/30 дней. Это НЕ подтверждённая фактическая частота API. Проверить число страниц на реальных типах магазинов.
3. Средний непрерывный поток 50400/86400=0.583 запросов/сек., но распределение по времени, Ozon sequential и WB 1/мин. могут давать очереди; моделировать p50/p95/p99 и rate-limited latency.
4. Capacity tests: 100/500/1000/3000 пользователей, 1.0/1.4/2.0 магазина на пользователя, сезонный пик, 15-минутные окна и cold backfill.
5. Параллельные web sessions, CPU, RAM, DB IOPS, queue depth, object storage 30/90/365 days, log retention, network egress, LLM cost per 1000 active shops.
6. Проводить отдельные smoke/stress/soak/chaos tests; 1000 сохранённых тестовых записей НЕ означает 1000 одновременных пользователей.
7. Показать Bear/Base/Bull бюджет в рублях: app, DB HA, workers, redis/broker, storage, KMS, backup, WAF/DDoS, monitoring, egress, LLM, support. Каждую цену подтверждать офертой/калькулятором и датой; без источника — допущение.
8. Следить за gross margin по 990/1990/2990, лимитами запросов и тяжёлыми клиентами; считать cost per active tenant и per million operations.
9. SLO: 99.9% availability target; freshness для ежедневного аудита отдельная SLI; RPO <= 15 min, RTO <= 60 min — только после DR drill.

## 10. Security, legal, incident response в РФ

1. До запуска вместе с российским юристом и ответственным по ПД установить: оператор/обработчик, основания обработки, договора, уведомления, обязательные политики, цели/сроки, права субъектов, порядок удаления.
2. В зависимости от реального содержания данных оценить применимость постановления 1119, приказа ФСТЭК 21 и других актов; не утверждать уровень защищённости без оценки угроз.
3. Локализация ПД, резервные копии, мониторинг и субподрядчики в РФ; документировать удалённый доступ разработчиков извне РФ и не включать его до правовой оценки.
4. Incident runbook: unauthorized access, exfiltration, ransomware, leaked API credential, loss of backup, marketplace outage, schema corruption, overbilling, incorrect financial statement.
5. Установить юридически корректные сроки и состав уведомлений РКН при утечке по актуальному закону; протестировать процесс заранее.
6. Разграничить публичный маркетинговый тезис «от 100 000 ₽» и договорное обещание; потребуются кейсы и независимая юридическая проверка рекламы.
7. Проверка penetration test по критическим ролям и личным кабинетам перед public beta; хранилище ключей и tenant-isolation — обязательные release blockers.

## 11. Непрерывные проверки / релизные gates

1. CI: lint, format, typecheck, static analysis, unit, property, schema contract, integration MockTransport, fixture regression, coverage, dependency scan, SBOM, secret scan, container scan.
2. Проверки на финансовые ошибки: negative signs, returns, corrections, same event across exports, COGS effective date, currencies, rounding, incomplete data, double counting.
3. Security tests: Broken Object Level Authorization/IDOR, SQLi, XSS, CSRF, SSRF, replay, stale sessions, manager invite revocation, secrets in logs, malicious CSV/XLSX, impersonation.
4. Chaos tests: worker killed mid-page, DB failover, queue unavailable, delayed network, 429 storm, token revoke, whole provider disabled, KMS unavailable, 1000 tenant backfill overload.
5. Load tests: реальные измерения latency, DB IOPS, queue lag, API effective throughput without violating quotas, memory, cost; приложить отчёт.
6. Backup restore DR: PITR and RTO/RPO drills with dated evidence.
7. No-go если missing tenant isolation, leaking keys, unverified profit displayed as realized, provider use on obsolete endpoint, impossible recovery, missing legal review.
8. Canary: 1% -> 10% -> 50% -> 100%, автоrollback по ошибкам и freshness, корректные non-breaking migrations.
9. Внедрение по этапам: synthetic only -> 3 разрешённых локальных кабинета read-only -> закрытая 10/50/100 продавцов -> 1000, по отдельному утверждению.
10. Никакие «прошло N тестов» не означает юридическую сертификацию, полную защищённость или гарантированную работоспособность при новом API.

## 12. Deliverables

1. Git repository с backend/frontend/workers/migrations/iac; `.env.example`, конфиги по средам, без секретов.
2. `README.md` + `ARCHITECTURE.md` + `API_CONTRACT_MATRIX.md` + `DB_SCHEMA.md` + `FINANCIAL_MODEL.md` + `SECURITY.md` + `LEGAL_RU_CHECKLIST.md`.
3. `docker compose` для local dev **без продовых секретов**; IaC для выбранного российского облака; CI/CD + скрипты безопасного деплоя/rollback.
4. `MONITORING.md`, `SLO.md`, `DISASTER_RECOVERY.md`, `INCIDENT_RESPONSE.md`, `COST_MODEL.md`, `RELEASE_GATES.md`.
5. Все результаты тестов и load runs с командой, версией, временем, конфигурацией и исходными логами без ПД.
6. Таблица обязательств: implemented / mock verified / live verified / pending / legal reviewed / rejected.
7. 90-дневный план с владельцами задач и приоритетами P0/P1/P2, критериями перехода и оценкой затрат.
8. Заключение: `GO / CONDITIONAL GO / NO GO` в production на основании доказательств, с отдельным указанием блокирующих задач.

**Ограничение текущей задачи:** не подключать настоящие кабинеты и не разворачивать публичный production без отдельного разрешения пользователя. Сначала полностью завершить безопасную реализацию, тестовый контур и dry-run; если отсутствует доступ к облаку, дать готовые IaC/процедуры, но не заявлять, что облако уже создано.
