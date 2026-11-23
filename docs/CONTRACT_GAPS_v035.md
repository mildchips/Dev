# Неподтверждённые контрактные вопросы до реального API

Факты сверены с публичными страницами разработчиков 18.11.2026, а не на живых кабинетах.

| Тема | Что реализовано сейчас | Что ещё проверить |
|---|---|---|
| WB `POST /api/finance/v1/sales-reports/detailed` | Запрос за период, cursor `rrdId`, 204, 60-сек. pacing | Точные права личных ключей, реальные форматы полей/дат/пагинации, многократность операций |
| WB `POST /api/finance/v1/sales-reports/list` | Не реализовано | Вывод списка `reportId`, периодичность, пагинация offset/limit |
| WB `.../detailed/{reportId}` | Не реализовано | Сверка с отчётами по ID, дополнительные события и лимиты |
| Ozon `/v1/finance/accrual/by-day` | Live HTTP транспорт, staging по `accrual_id` | Схема текущего ответа, курсор `last_id`, пооперационная классификация, итоговые суммы |
| Ozon `/v1/finance/accrual/types` и `/postings` | Не реализовано | Справочник и связь начислений с отправлениями, исключение двойного учёта |
| Ozon `/v1/roles` | Только документирована возможность `expires_at` | Реальная проверка ролей и даты expiry через read-only метод |
| Ozon отчёты реализации | Не реализовано | 18.11.2026 объявлены `/v1/report/finance/realization/list`, `/number/create` и `/number/posting/create`: обновить сверку в v0.3.6 |
| OAuth для массового SaaS | Нет | Разработка приложения, политики провайдера, хранение и отзыв refresh-токенов |

**Источники:**
- https://dev.ozon.ru/news/783-Izmeneniia-v-metodakh-dlia-raboty-s-finansovymi-otchetami-v-Seller-API/
- https://dev.ozon.ru/news/784-Izmeneniia-v-srokakh-deistviia-kliuchei-Seller-API/
- https://dev.ozon.ru/news/802-Zagolovki-dlia-upravleniia-reit-limitami-v-Seller-API/
- https://dev.ozon.ru/news/844-Novye-metody-polucheniia-otchetov-o-realizatsii-v-Seller-API/
- https://dev.wildberries.ru/docs/openapi/financial-reports-and-accounting

При любой неизвестной структуре — остановить импорт с `ContractError`; не писать нулевую прибыль и не пропускать операцию молча.
