# SnipeIT Inventory Gateway v1.0.0

Я разработал SnipeIT Inventory Gateway, потому что мне была нужна инвентаризация
Windows-компьютеров, которая продолжает работать вне локальной сети и не требует
хранить Snipe-IT API token или SSH key на каждом endpoint.

## Назначение и архитектура

Я разделил систему на недоверенный Windows-агент и доверенный server-side
Gateway. Агент собирает inventory и отправляет encrypted event через
`HTTPS POST /api/v1/events`. Если HTTPS недоступен, он отправляет тот же
envelope через SMTP с читаемой темой и зашифрованным attachment. Если оба
транспорта недоступны, агент сохраняет событие локально и повторяет его позднее.

HTTPS ingest и IMAP collector кладут события в одну durable SQLite queue. Worker
выполняет dedup по `event_id`, проверяет generation/watermark и только затем
пишет через локальный Snipe-IT API.

## Что вошло в v1.0.0

- Windows agent, installer, SYSTEM scheduled task, state/log/local retry queue,
  rollback и uninstall;
- узкий HTTPS endpoint, Nginx TLS/size/rate/connection limits;
- SMTP/IMAP fallback и MIME classifier, который не трогает unrelated mail;
- SQLite WAL queue, lease recovery, retry/backoff, dead-letter и retention;
- server-side asset create/update, check-in/checkout, строгий формат username и
  подтверждение нового владельца тремя событиями минимум за 24 часа;
- защита от смены владельца «на никого», ежедневного перескакивания и повторных
  писем;
- русские брендированные отчёты EXAMPLE INVENTORY, верхние KPI и понятные IMAP-папки;
- уведомления о dead-letter, повторяющихся ошибках, восстановлении и
  еженедельная сводка по всем компьютерам;
- systemd hardening, logrotate, backup/restore, install/update/rollback;
- migration dry-run с 1.3.3;
- русская документация, архитектурная схема, unit/integration/security tests,
  CycloneDX SBOM, test report и SHA-256 checksums артефактов.

## Безопасность

Я использую AES-256-CBC Encrypt-then-MAC. Из случайного master key через HKDF
выводятся независимые encryption и HMAC-SHA256 keys; HMAC проверяется до
расшифрования. В envelope есть `key_id` и поддержка rotation. Base64 является
только транспортной кодировкой.

Agent не получает Snipe-IT token и SSH key. Payload проходит строгую schema,
endpoint не управляет произвольными API paths, а server самостоятельно решает,
можно ли менять владельца. Production secrets не входят в repository, tests,
artifacts или logs.

## Установка и миграция

Я рекомендую сначала выполнить локальные tests, затем `install.sh --dry-run`,
`migrate-1.3.3.py --dry-run`, проверить systemd/Nginx configuration и провести
canary на тестовом asset. Порядок установки, backup, restore и rollback описан в
`docs/OPERATIONS-RU.md`; production gates — в `docs/READINESS-RU.md`.

## Известные ограничения

Это активная разработка и release candidate, а не обещание готового
production-развёртывания.

- Перед production нужно сверить model/status IDs и custom field handles своей
  Snipe-IT instance.
- Live SMTP/IMAP и edge NAT зависят от конкретной инфраструктуры и требуют
  отдельного staging.
- Публикация release не включает production services автоматически.
- Production cutover я выполняю только после полного staging и отдельного
  явного подтверждения.
