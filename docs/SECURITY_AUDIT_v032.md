# Security Audit — AI Profit Office v0.3.2

Дата: 18.11.2026. Область: локальное приложение и vault для WB/Ozon. **Не является pentest или сертификатом безопасности.**

## Critical: состояние до исправления

В v0.3.1 сервер по умолчанию связывался с `127.0.0.1`, однако API не требовал авторизации. Любой процесс, способный обратиться к локальному серверу, мог прочитать магазины/финансовые строки и изменить себестоимость, импорт или удалить магазин. Это неприемлемо для реальных финансовых данных. Существующие проверки изоляции по `store_id` не подтверждали изоляцию по личности пользователя.

## Реализовано в v0.3.2

| Защита | Реализация | Проверено |
|---|---|---|
| Default deny | Пока локальная парольная фраза не настроена, финансовые API блокируются | TestClient |
| Локальный вход | scrypt с уникальной солью, проверка без хранения пароля в открытом виде | TestClient |
| Сессия | Случайная cookie HttpOnly + SameSite Strict, хэш сессии в SQLite; 12 ч максимум и 30 мин простоя | TestClient |
| Защита от перебора | После 5 ошибок — пауза 60 сек; это *минимальная* локальная мера | TestClient |
| CSRF и cross-origin | X-AIPO-CSRF для POST/PUT/PATCH/DELETE; блокировка чужих Origin и Fetch-Site | TestClient |
| Локальный хост | Uvicorn `127.0.0.1`; TrustedHost localhost / 127.0.0.1; отсутствие CORS-разрешений | TestClient |
| Заголовки | no-store, nosniff, X-Frame-Options DENY, Referrer-Policy no-referrer | Code review |
| Файловые разрешения | Директория 0700; SQLite 0600; ключ vault 0600 | TestClient |
| API-ключи | AES-256-GCM локально, не в HTTP интерфейсе; работа через CLI; 4 уровня стопа | TestClient |
| Kill-switch | global/platform/store/credential и локальное неотменяемое revoke encrypted payload | TestClient |
| Журнал оператора | Создание магазина, импорт, изменение себестоимости, статусы задач, удаление | TestClient |
| Удаление магазина | Запрос требует точного совпадения имени магазина | TestClient |
| Импорт | Ограничение на 5 МБ/15 000 строк; XLSX распаковка до 32 МБ, <=200 ZIP members; экспорт CSV защищает от formula injection | Unit tests / review |

## Важные ограничения — эти пункты ещё НЕ закрыты

1. **Нет SaaS-авторизации / RBAC.** Есть одна локальная парольная фраза для оператора одного компьютера. Нельзя обслуживать 1000 независимо аутентифицированных продавцов. Для production потребуются пользователи, tenant ACL, права owner/manager/auditor и серверная проверка доступа к каждой операции, плюс тесты cross-tenant IDOR.
2. **Нет MFA, TLS, внешнего gateway и WAF.** Локальная HTTP-сессия оправданна только на loopback; запрещено публиковать порт 8765 в Интернет, проксировать наружу или делать tunnel.
3. **Local Vault ≠ cloud KMS.** Шифровальный master key хранится на той же машине, что и зашифрованные credentials. Компрометация устройства может раскрыть и базу, и ключ. Для production нужны KMS/HSM, отдельные права воркеров, политики ротации, секреты в памяти как можно меньше времени.
4. **Нет реальных запросов к WB/Ozon.** Kill-switch проверен на wrapper `Vault.with_authorized`, но не на сетевой dispatch, очередь и in-flight запросы. Stop не отзывает токены на площадке.
5. **Локальный журнал не неизменяем.** Любой пользователь с файловым доступом к SQLite способен изменить запись. Production требует append-only/WORM или централизованный SIEM.
6. **Нет тестирования проникновения / SAST dependency scanning / threat simulation.** Тесты — автоматические регрессии плюс мокированный браузерный UI.
7. **Нет полноценного жизненного цикла данных:** retention/deletion logs, бэкап-шифрование, тест восстановления, договоры и юрпроверка обработки коммерческих данных.
8. **Защита Excel-файлов неполная.** Хотя есть лимит распаковки, требуется sandbox-обработка и сканирование потенциально вредоносных документов перед публичным SaaS.
9. **Отзыв ключа в локальном vault не равен отзыву в маркетплейсе:** при подозрении на утечку нужно срочно перевыпустить ключ в самом WB/Ozon. Старые encrypted bytes могут сохраниться в бэкапах; полный forensic purge отдельно.

## Threat model: приоритеты

| Риск | После v0.3.2 | Оставшиеся действия |
|---|---|---|
| Сторонний доступ к localhost API | Уменьшен (login/CSRF/Host) | Loopback-only + не публиковать наружу |
| Кража API-токенов через UI | Уменьшен (UI не принимает токены) | Cloud KMS, ограниченные scope/permissions |
| Использование токена после остановки | На уровне локального wrapper блокируется | Проверка перед каждым настоящим API-вызовом, egress-stop |
| Межклиентский IDOR в SaaS | **Не решён** | IdP, RBAC, tenant-aware middleware / DB RLS |
| Ложная денежная находка | Уменьшен (блокирующие тесты) | Reconciliation + human review |
| Вредоносный файл/CSV injection | Частично уменьшен | Sandbox, rate limits, end-to-end security |
| Сбой/восстановление | **Не решён** | Шифрованные бэкапы, DR drills, SLO |

## Действия при инциденте API-ключа

1. `./scripts/kill-all.sh` из папки приложения; команда блокирует *новые разрешённые диспетчеризации* локального vault.
2. **Немедленно отозвать / перевыпустить ключи** в WB/Ozon, если есть подозрение, что ключ мог быть раскрыт (локальный stop недостаточен).
3. Проверить `python -m app.integration_cli status` и `python -m app.integration_cli audit`.
4. Убедиться, что секрет не попал в файлы, bash history, баг-репорт, `.env`, скриншоты, LLM и backup.
5. После ревокации в кабинете добавить новый ключ через скрытый ввод и только затем снять необходимые stop-флаги.

## Требования перед SaaS на 1000 пользователей

- Identity/MFA, tenant-roles, membership, invite/revoke и deny-by-default; авторизация на каждой сущности и URL.
- PostgreSQL с `tenant_id` по каждой бизнес-сущности и при необходимости RLS; автоматические cross-tenant тесты.
- KMS envelope encryption per integration, секреты только у worker-adapter, не в AI agent/tool output.
- Central scheduler/queue с idempotency, rate limit по токену и API методу, distributed stop signal, короткий timeout; egress allowlist.
- Secret rotation, audit trail, metering (per tenant), DPA/комплаенс, encrypted backups + restore drills, incident response.
- Никаких права изменения цен/рекламы/средств в первом production релизе, READ ONLY + независимые проверки.

Референс внешней практики: [OWASP Multi-Tenant Security Cheat Sheet](https://cheatsheetseries.owasp.org/cheatsheets/Multi_Tenant_Security_Cheat_Sheet.html), [OWASP ASVS 5.0](https://github.com/OWASP/ASVS/tree/master/5.0/en).
