# Контрактная матрица Ozon / WB — 18.11.2026

**Статусы:** OPEN-SOURCE-VERIFIED = наличие метода/общая структура подтверждены открытыми источниками; MOCK-VERIFIED = локальная тестовая реализация; LIVE-UNVERIFIED = не проверено через настоящие токены.

| Площадка | Read-only метод | Назначение / курсор | Авторизация | Конец страниц / ограничения | Статус |
|---|---|---|---|---|---|
| Ozon | `POST /v1/finance/accrual/by-day` | Начисления за 1 день, `last_id` | `Client-Id`, `Api-Key` | Пустой `last_id` в опубликованных примерах; публичное обсуждение неоднозначных случаев; последовательные вызовы | OPEN-SOURCE + MOCK, LIVE-UNVERIFIED |
| Ozon | `/v1/finance/accrual/types` | Справочник типов начислений | Seller API | **Отдельная реализация TODO**; без него классификация начислений неполная | OPEN-SOURCE, NOT IMPLEMENTED |
| Ozon | `/v1/finance/accrual/postings` | Начисления по отправлениям | Seller API | **Отдельная реализация TODO** | OPEN-SOURCE, NOT IMPLEMENTED |
| WB | `POST /api/finance/v1/sales-reports/detailed` | Детализация за период; `rrdId` | `Authorization` token Finance scope | Ожидаемый HTTP 204 при окончании; лимит 1 запрос/мин на кабинет по опубликованной документации | OPEN-SOURCE + MOCK, LIVE-UNVERIFIED |
| WB | `POST /api/finance/v1/sales-reports/list` | Список финансовых отчётов | WB Finance scope | 1 запрос/мин. по документации | OPEN-SOURCE, NOT IMPLEMENTED |

**Финансовые особенности:** WB JSON camelCase, деньги могут быть строками; Ozon начисления — вложенные сущности и типы. Ни одна отдельная строка не должна автоматически превращаться в «выручку» или «экономию». Текущий `offline_sync.py` сохраняет только минимальные синтетические поля в staging, НЕ делает признание выручки и НЕ переносит эти события в основной финансовый ledger. Новые поля не приводят к ложным значениям.

**Ненадёжные и устаревшие методы:** `/v3/finance/transaction/list` и `/v3/finance/transaction/totals` у Ozon отключены с 08.09.2026; старый WB GET `/api/v5/supplier/reportDetailByPeriod` заменён на v1 POST. Сверять актуальную документацию перед каждой итерацией live.

**Сроки ключей Ozon:** с 03.09.2026 срок новых Seller API ключей — 3 месяца, требуется UX-ротация и уведомления за 30/14/7 дней (в текущем mock ещё не реализована).

**Источники для проверки (публичные):**
- Ozon 24.08.2026, изменения finance: https://dev.ozon.ru/news/783-Izmeneniia-v-metodakh-dlia-raboty-s-finansovymi-otchetami-v-Seller-API/
- Ozon ключи 2026: https://dev.ozon.ru/news/784-Izmeneniia-v-srokakh-deistviia-kliuchei-Seller-API/
- Ozon новые методы и известные вопросы по JSON: https://dev.ozon.ru/community/2008-Novye-beta-metody-dlia-polucheniia-nachislenii/
- WB official release channel: https://t.me/wb_api_notifications/452
- WB API docs (доступна международная зеркальная редакция; домены для РФ брать только `wildberries.ru`): https://dev.wildberries.cn/docs/openapi/documents-and-accounting
- WB русская документация: https://dev.wildberries.ru/docs/openapi/financial-reports-and-accounting

**До первого live-теста перепроверить:** реальные поля response Ozon/WB на актуальной дату, start/terminal cursor Ozon, типы начислений, обязательные поля, по-клиентские rate limits, TTL курсора, объём одной выдачи, реальные ошибки, необходимую категорию WB Finance, разрешения Seller API Ozon. Сделать короткий ручной READ ONLY canary на одном кабинете с согласия владельца и локально сравнить с отчётом.
