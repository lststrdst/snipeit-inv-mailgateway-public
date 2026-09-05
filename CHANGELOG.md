# История изменений

## [1.0.0] — 2026-08-31

Это первый самостоятельный релиз системы, которую я разработал для доставки
инвентаризации Windows-компьютеров в локальный Snipe-IT без зависимости от VPN
и без Snipe-IT token или SSH key на endpoint.

### Что я добавил

- Я реализовал основной HTTPS ingest через единственный
  `POST /api/v1/events` и резервный SMTP/IMAP transport.
- Я объединил оба транспорта в durable SQLite queue с event_id dedup,
  generations, watermark, lease, retry/backoff и dead-letter.
- Я реализовал AES-256-CBC Encrypt-then-MAC, независимый HMAC-SHA256,
  случайные salt/IV, `key_id`, rotation и per-device key binding.
- Я добавил строгую payload/config schema и запретил endpoint управлять
  произвольными Snipe-IT API paths.
- Я добавил IMAP MIME classifier с фиксированным приоритетом папок и
  совместимостью со старыми темами, не затрагивая unrelated mail.
- Я реализовал полный Windows deployment: SYSTEM task, machine-DPAPI SMTP
  credential, state/log/local queue, installer, rollback и uninstall.
- Я перенёс решение о владельце на server side: служебные учётные записи не
  назначаются, а offboarding требует authoritative directory state.
- Я добавил Nginx configuration, systemd hardening, logrotate, healthcheck,
  backup/restore, installer/update/rollback и миграцию с 1.3.3.
- Я подготовил русскую документацию, архитектурную схему, unit/integration/
  security tests и собираемые wheel/source artifacts.

### Безопасность

- Я не включаю production passwords, tokens, HMAC/AES keys или SSH keys в Git.
- Перед push я проверяю staged content и историю на секреты.
- После повторного dependency audit я поднял минимальные версии cryptography,
  pytest и installer pip до исправленных веток и повторно проверил окружение.
- Worker теперь раз в час применяет SQLite retention к обработанным,
  отклонённым и stale-событиям; dead-letter сохраняется для ручного разбора.
- Windows-агент ограничивает локальную ротацию пятью архивами `agent.log.1`–
  `agent.log.5` по 2 MiB каждый.
- Пользовательское имя, Scheduled Task, metadata и User-Agent унифицированы как
  `SnipeIT Inventory Gateway`.
- Я добавил строгий формат пользовательских учётных записей и явный список
  `non_standard_accounts` для `LegacyUserA` и `LegacyUserB`; остальные
  нестандартные имена не используются для checkout.
- Я добавил server-side подтверждение владельца: три разных события минимум за
  24 часа, сброс нестабильного кандидата и полный запрет смены «на никого».
- Я заменил старые письма русскими HTML-отчётами в стиле EXAMPLE INVENTORY, добавил
  верхние KPI, русские IMAP-папки и отдельную дедупликацию всех отчётов.
- Я сократил почтовое дерево до `Reports`, `Weekly Reports` и `Errors`, ввёл
  стабильные метки тем и запретил смешивать личную, IMAP и SMTP identities.
- Я потребовал разные пароли сервисных IMAP/SMTP ролей, точный `To` без `Cc` и
  добавил читаемый error report для временного retry, rejection и dead-letter.
- Для публичной редакции я дополнительно удаляю домены, IP, адреса почты,
  пользователей, OU/DN, закрытые paths/IDs и другие признаки инфраструктуры.

### Известные ограничения

- До production cutover я должен подтвердить реальные Snipe-IT IDs/field
  handles и выполнить canary на тестовом asset.
- Live IMAP/SMTP, TLS, split DNS и edge NAT требуют отдельной staging-проверки.
- GitHub Release не означает автоматическое включение production services.
