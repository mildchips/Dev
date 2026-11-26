# R04-RFC · маршруты и права для нового периметра

Проект для согласования. Ниже перечислены регистрации из `app/server.py`.
Разрешения относятся к будущему API. Обозначения ролей: **O** owner организации,
**M** manager назначенного магазина, **A** auditor назначенного магазина.
Финальный набор разрешений зависит от согласования с Партнёром А и эталонов L03.

Для каждой строки с магазином обязательны два отрицательных теста:

- **Чужой ресурс.** Запросить указанный маршрут с известным ID магазина другого
  клиента, затем повторить с чужим вложенным ID внутри своего магазина, где
  применимо. Ожидать общий 404 без финансовых строк, файла, задачи или записи.
- **Отзыв.** Сохранить сессию разрешённого пользователя, затем отозвать членство
  или scope, дождаться коммита и повторить запрос. Ожидать общий 404. Для отзыва
  самой сессии ожидать 401. Для мутаций сравнить ledger/audit, очереди и storage
  до и после. Отказ может писать отдельное обезличенное событие безопасности.

| Метод | Текущий путь | Предлагаемое разрешение и предмет проверки |
|---|---|---|
| GET | `/` | Оболочка без данных. С чужой/отозванной сессией API возвращает отказ и не раскрывает состояние |
| MOUNT | `/assets` | Только статические ресурсы. Экспорты, vault и пользовательские файлы недоступны |
| GET | `/api/health` | Минимальный технический статус. Ответ одинаков для разных сессий и не раскрывает tenancy |
| GET | `/api/system/posture` | Внутренний operational endpoint. С внешнего ingress отсутствует для любой сессии |
| GET | `/api/auth/status` | Текущий серверный сеанс. Подмена tenant не расширяет статус. Отзыв даёт статус выхода |
| POST | `/api/auth/login` | Legacy local only. Новый вход через IdP. Чужие параметры и отозванный субъект не создают сессию |
| POST | `/api/auth/logout` | Собственная сессия, защита от CSRF. Нельзя закрыть чужую сессию, повтор отозванной не даёт полномочий |
| GET | `/api/stores` | O/M/A, scoped список. Не включает чужие и отозванные магазины |
| POST | `/api/stores` | O в активном tenant. Подмена tenant запрещена, после отзыва запись отсутствует |
| DELETE | `/api/stores/{store_id}` | O, отдельный lifecycle архивации/удаления. Legacy delete остаётся локальным |
| GET | `/api/local/audit` | Legacy local only. Новый tenant audit для O, scoped события. Чужие и отозванные недоступны |
| GET | `/api/stores/{store_id}/imports` | O/M/A, список импортов |
| POST | `/api/stores/{store_id}/imports` | O/M, импорт с лимитом чтения файла |
| PUT | `/api/stores/{store_id}/costs` | O/M с правом изменения себестоимости |
| POST | `/api/stores/{store_id}/scenarios/compare` | O/M/A, расчёт сценария без записи |
| PUT | `/api/stores/{store_id}/cost-revisions` | O/M с правом записи новой ревизии |
| GET | `/api/stores/{store_id}/actions` | O/M/A, список действий |
| POST | `/api/stores/{store_id}/actions` | O/M, создать действие |
| PATCH | `/api/stores/{store_id}/actions/{action_id}` | O/M, scope проверяется и для action_id |
| GET | `/api/stores/{store_id}/cost-revisions` | O/M/A, чтение ревизий |
| GET | `/api/stores/{store_id}/costs` | O/M/A, чтение себестоимости |
| GET | `/api/stores/{store_id}/finance` | O/M/A, сводка |
| GET | `/api/stores/{store_id}/finance/journal` | O/M/A, журнал provenance |
| POST | `/api/stores/{store_id}/finance/reports` | O/M, загрузка нормализованного отчёта |
| POST | `/api/stores/{store_id}/finance/controls` | O/M с явным правом загрузки независимого контроля, scope отчёта |
| POST | `/api/stores/{store_id}/finance/batches` | O/M с правом записи партии |
| GET | `/api/stores/{store_id}/dashboard` | O/M/A, сводка и производные показатели |
| POST | `/api/stores/{store_id}/reviews/{signal_key:path}` | O/M, scope сигнала |
| GET | `/api/stores/{store_id}/ledger` | O/M/A, строки журнала |
| GET | `/api/stores/{store_id}/ledger.csv` | Заменить сервисом с отдельным export permission, повторная проверка на create/status/download/renew |
| POST | `/api/demo` | Только synthetic lab; отсутствует во внешнем ingress для любой сессии |

Перед переключением нужны OIDC/MFA, ротация сессии, отзыв и восстановление
владельца. Отдельные задачи — repository и RLS всех финансовых таблиц,
миграционный паритет с L03, KMS/IAM только у connector, жизненный цикл
экспорта. Каждый новый HTTP-маршрут проходит scoped проверку и recheck при
выполнении отложенной операции. Протокол повторного допуска утверждают оба
партнёра отдельно. Статус публичного запуска — NO GO.
