# AI Profit Office v0.3.7 — отчёт проверки

Дата: 23.11.2026. Окружение Linux x86_64; Python 3.13.5 и 3.12.14. **NO GO для публичного SaaS.** Целевой продукт — для российских селлеров WB/Ozon. Реальные ключи, ПД клиентов, внешние API и LLM не использовались. Новые зависимости не устанавливались.

## Основа и объём изменения

Источник — `AI_Profit_Office_v0.3.6_READ_ONLY.zip`, 2 050 839 байт, SHA-256 `36c1cc271f88b44a6d181db9b8a7dad1478753d25d8799dd0b403c150c890258`. Исходный архив и прежние файлы не заменялись. Новый релиз собран в отдельной папке из этого архива.

Базовый запуск v0.3.6: `../../.venv313/bin/python scripts/test_offline.py -q` — **230 passed**, 0 failed, 0 skipped, 1 warning, 6.98 s. Версия из Library README подтверждала отсутствие production RBAC/MFA/KMS/RLS.

Изменения: отдельный `saas_lab/`, новый веб-экран, core roles/invites/sessions/store ACL, SQLite job queue, KMS interface/envelope, encrypted lab backup/restore, синтетические load/DR scripts; документация и production gates. Финансовая схема и старый vault не мигрировали. Версия приложения/health/posture и маркировка рынка актуализированы; тест health обновлён под новый version string. Предыдущие инструкции/отчёты сохранены как история.

## Выполненные команды

Команды исполнялись из корня нового релиза; `../../.venv313/bin/python` и `../../.venv/bin/python` — фактически существовавшие среды в рабочем окружении. На компьютере пользователя следует использовать `python` из созданного venv.

| Команда | Результат |
|---|---|
| `../../.venv313/bin/python scripts/test_offline.py -q --junitxml=reports/pytest_v037_python313.xml` | **260 passed**, 0 failed, 0 skipped, 1 warning; 14.80 s |
| `../../.venv/bin/python scripts/test_offline.py -q --junitxml=reports/pytest_v037_python312.xml` | **260 passed**, 0 failed, 0 skipped, 1 warning; 12.68 s |
| `../../.venv313/bin/python scripts/load_lab_v037.py --output reports/load_lab_v037.json` | PASS проверок результатов; реальный замер in-process HTTP/SQLite |
| `../../.venv313/bin/python scripts/restore_drill_v037.py reports/restore_drill_v037.json` | PASS synthetic lab backup/restore и manual resume |
| `node --check saas_lab/lab.js` | PASS syntax |
| `node scripts/frontend_smoke_v036.cjs` | PASS: финансовые состояния, точные суммы, XSS escaping, actual render; это DOM harness |
| `../../.venv313/bin/python -m compileall -q app saas_lab scripts tests` | PASS |
| `../../.venv313/bin/python scripts/browser_lab_v037.py` | **NOT VERIFIED**: Chromium завершился до открытия страницы, socket/ptrace Operation not permitted |

Предупреждение обоих pytest runs: FastAPI/Starlette deprecation существующего httpx TestClient. Зависимости lock оставлены прежними; миграция на иной TestClient не выполнялась. Это не ошибка тестов.

## Что проверяют 30 новых cases

- Owner/manager/auditor; scope одного магазина при наличии второго; отказ чужой организации, неизвестной/служебной роли.
- Привязка инвайта к логину, TTL, одноразовость, невозможность перезаписать membership. Реальной email verification нет: пользователя создаёт доверенная CLI.
- Отзыв членства закрывает чтение, запуск и commit задачи; membership expiry проверяется при commit.
- HTTP negative matrix: stores/jobs/export/invites/revoke/audit/stop, подмена tenant/store; generic denial без чужого названия. Экспорт — JSON задачи после текущей ACL-проверки, не production object download.
- Анонимный запрос, отсутствующий CSRF, неверный Host/Origin, cross-site, remote peer; logout и expiry инвалидируют сессии; lockout после пяти ошибок.
- Конкурентная идемпотентная постановка 24 вызовами и claim 16 вызовами; ровно одна задача/аренда.
- Recovery после reopen базы и истечения lease, stale worker/repeated completion, backoff, три попытки и DEAD; stop/resume не обходит лимит попыток. Время моделируется clock; это не SIGKILL/отказ кластера.
- Tenant stop во время RUNNING, независимость другой организации, fencing старой аренды после resume.
- Пароль не хранится открыто, session token хранится хешем, аудит защищён от UPDATE/DELETE через штатный SQL. Защита от владельца файла/DBA не заявляется.
- AAD tenant/store/provider/version/key_id; подмена каждого поля отвергается; тестовая ротация envelope и отказ при недоступном FakeKMS. Облачного KMS/IAM нет.
- Зашифрованная копия, неверный пароль, подмена ciphertext, отсутствие перезаписи, integrity/FK check, восстановление магазинов; старые сессии/инвайты/lease не действуют, делегаты отключены, jobs paused, tenants stopped. Отдельный тест снимка до отзыва менеджера исключает восстановление его membership.

## Измеренная синтетическая нагрузка

Источник: `reports/load_lab_v037.json`, 23.11.2026 07:40:10 UTC. 100 независимых организаций; 2 000 запросов; 16 конкурентных потоков TestClient; Linux сообщает 9 logical CPUs. В тесте заблокированы внешние connect/DNS, TCP-сервер не используется. Подготовка аккаунтов и последующее последовательное выполнение jobs не включены в HTTP throughput.

| Метрика | Факт |
|---|---:|
| Длительность HTTP-фазы | 14.102 s |
| Запросов/сек | 141.82 |
| p50 | 34.990 ms |
| p95 | 467.009 ms |
| p99 | 1 168.837 ms |
| Ожидаемые отказы чужого доступа | 500 |
| Непредвиденные статусы | 0 |
| Синтетические jobs завершены после HTTP-фазы | 500 из 500 |

Это успешная проверка результатов данного профиля, без утверждения latency SLO. p99 превышает секунду. Число аккаунтов не равно одновременным пользователям. Нет ingress/TLS, PostgreSQL/RLS, object storage, KMS, брокера, provider latency/quotas, сетевых ошибок, soak/chaos и отказа зоны. Предел мощности публичного продукта **не измерен**.

## Измеренное восстановление

Источник: `reports/restore_drill_v037.json`, 23.11.2026 07:40:11 UTC. Отдельная временная synthetic SQLite база, один магазин, одна RUNNING задача. Копия 86 078 байт; шифрование/создание 0.2050 s; расшифрование/запись новой базы 0.1113 s. Проверены integrity, наличие магазина, отказ прежней сессии, отказ stale lease, paused и manual resume. Пароль вымышленный, зафиксирован только в воспроизводимом тестовом сценарии.

**Это не RTO/RPO production.** Нет PITR/WAL replay, удалённого backup, потерянного primary, восстановления IAM/KMS, отзыва ключей площадки или полной загрузки трафика. Отключение делегатов не решает независимо identity recovery владельца из старого снимка: production нужен внешний реестр отзывов и отдельная проверка владельца.

## Непроверенное и блокеры

1. Финансовый UI/API остаётся single-user localhost; lab не интегрирован с его маршрутами и не делает его SaaS.
2. PostgreSQL отсутствует в среде. `001_rls_DRAFT.sql` не исполнялся; намеренный ROLLBACK; не полная миграция.
3. KMS — интерфейс и crypto mechanism; FakeKMS только в тестах. Нет production vault/rotation/IAM.
4. MFA/OIDC/password recovery, public TLS, cloud HA/PITR, распределённые quota/stop/fairness, production storage/export ACL не реализованы.
5. Chromium не стартовал из-за системного ограничения, новый web UI визуально не подтверждён. Подготовленный browser script не считается PASS.
6. MacBook M1/macOS, live WB/Ozon, коммерческая партнёрская авторизация, независимый pentest, dependency/container audit, SBOM/подписанные образы не проверялись в этом запуске.
7. Административный гайд содержит источники и план; сведения об операторе/поданные уведомления/договоры/ККТ/юридическое заключение отсутствуют. Уведомления и письма третьим лицам не отправлялись.

Подробный список: `deployment/RELEASE_GATES_v037.json`. Публичное развёртывание остаётся заблокированным даже при ручной смене полей этого файла. Доказательства Linux/mock/lab нельзя переименовывать в production/live/compliance PASS.
