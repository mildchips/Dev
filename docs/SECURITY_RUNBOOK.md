# Runbook: локальный защищённый режим v0.3.2

Сервер слушает только `127.0.0.1:8765`; веб-API требует локальный пароль и CSRF. Это **не** публичный SaaS. До ввода токенов настройте локальную парольную фразу (`python -m app.local_auth init-password`), используйте FileVault / полное шифрование диска и отдельного пользователя Mac.

Токены добавляются **только** CLI, никогда не через браузер, LLM или файлы проекта:

```bash
python -m app.integration_cli init-key
python -m app.integration_cli add --store STORE_UUID --provider WB --label wb-main
python -m app.integration_cli add --store STORE_UUID --provider Ozon --label oz-main
python -m app.integration_cli status
```

Vault 0600, данные в SQLite зашифрованы AES-256-GCM, а локальный master key хранится на том же устройстве. Доступ к компьютеру/ключу = риск компрометации. Изолируйте резервные копии.

**Экстренно выключить все новые обращения через vault:**

```bash
./scripts/kill-all.sh
```

При подозрении на компрометацию ОБЯЗАТЕЛЬНО отозвать API-токены в самих кабинетах WB и Ozon. Software stop не отзывает ключи у провайдера и не завершает in-flight запросы. В v0.3.2 реальных API-вызовов вообще нет.

```bash
python -m app.integration_cli stop --scope platform --id WB
python -m app.integration_cli stop --scope store --id UUID
python -m app.integration_cli stop --scope credential --id TOKEN_UUID
python -m app.integration_cli rotate --id TOKEN_UUID
python -m app.integration_cli revoke --id TOKEN_UUID
python -m app.integration_cli audit
```

Восстановление после разбора инцидента: `python -m app.integration_cli resume --scope global --id '*'`. Отдельные флаги сохраняются до индивидуального снятия.

Если пароль утерян, `python -m app.local_auth reset-password` — восстановление через локальный терминал; все прежние сеансы аннулируются. Не используйте один и тот же пароль для аккаунтов маркетплейсов. Дополнительные риски — `SECURITY_AUDIT_v032.md`.
