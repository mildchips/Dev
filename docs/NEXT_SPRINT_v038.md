# v0.3.8 — Integration perimeter and real PostgreSQL staging

Целевая аудитория: российские селлеры WB/Ozon. Публичный запуск v0.3.7 заблокирован.

1. Поднять отдельный синтетический PostgreSQL staging в выбранном разрешённом окружении. Закрепить версию, миграционную/API/worker/backup роли; выполнить реальные RLS и pool leakage tests. Текущий SQL — только dry-run stores/reports.
2. Подключить IdP/OIDC, MFA и lifecycle учётной записи; доверенные server claims, проверка issuer/audience/expiry/revocation, session rotation/recovery. Не переносить lab login на публичный адрес.
3. Обернуть все операции финансового ядра новым репозиторием с tenant/store context; провести миграционные проверки, сохранить provenance/копейки/карантин. Ни один старый маршрут не публиковать до нового negative test.
4. Реальный KMS адаптер и versioned credential repository; workload identity только у connector, AAD, ротация/отзыв, отрицательные IAM тесты. Никаких ключей в конфиге/CI/LLM.
5. Распределённые очередь/stop/quota, tenant fairness и atomic page/checkpoint/ledger; повторная проверка прав при каждом dispatch, перед retry/commit; подписанный job payload, DLQ review.
6. Объектное хранилище, export service и отзыв ссылок; access checks на create/status/download/renew; шифрование/TTL/меньшие права/ограничение парсинга.
7. PostgreSQL HA/PITR + независимый backup в РФ, DR с потерей primary; внешний реестр отзывов/ключей вне откатываемой копии, reapply deletion, identity proof владельца. Измерить RPO/RTO.
8. Реальная нагрузка ingress/TLS/PG/KMS/broker: 100/500/1000/3000 клиентов, read/write/exports/backfill, 1.4 магазина/клиента, soak/chaos; включить планы стоимость/квоты. Не посылать load в WB/Ozon.
9. Параллельно завершить юридический реестр из LAUNCH_GUIDE_RU_v037.md, определить оператора/КЗ доступ/API условия/платежи. Тексты с пустыми реквизитами не публиковать как утверждённые.
10. Вернуться к проверенным provider contracts и review карантина из NEXT_SPRINT_v037.md: задачи не отменены, перенесены из-за приоритета безопасности. Реальный canary — только после отдельного согласия и локального безопасного ввода ключа.

Критерий завершения: новое финансовое API проходит production identity + tenant/store isolation на настоящем PostgreSQL, KMS IAM/rotation и DR имеют проверяемые отчёты. Закрытая beta — отдельное решение по gates; 1000 клиентов не объявлять готовыми только по числу unit tests.
