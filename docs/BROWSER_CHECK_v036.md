# Визуальная проверка v0.3.6

Попытка: Playwright Chromium, настоящая локальная FastAPI-служба, синтетические данные, планировались viewport 1440×1000 и 390×844. Browser не запустился: `chrome/browser/process_singleton_posix.cc: socket() failed: Operation not permitted`.

Результат: BLOCKED, не PASS. Скриншоты v0.3.6 не созданы. Исторические изображения прежних релизов не являются проверкой нового экрана.

Дополнительный `node scripts/frontend_smoke_v036.cjs` прошёл: пять состояний, скрытие заблокированного вклада, точное число >2^53, экранирование ID и render. Это минимальный DOM harness без layout engine, не визуальная/мобильная проверка.
