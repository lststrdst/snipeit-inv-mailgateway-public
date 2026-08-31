# Миграция с 1.3.3

Новый проект не импортирует исходники, systemd units, SQLite schema или Git
history старого IMAP relay. Из 1.3.3 берутся только проверенные бизнес-правила:
стабильный event ID, защита от stale inventory, имена папок, сценарии
offboarding/владельца и совместимые темы.

`scripts/migrate-1.3.3.py --dry-run` проверяет наличие необходимых полей, не
печатает значения и ничего не пишет. Реальный запуск читает старый закрытый
server config только на сервере, генерирует новый random master key и создаёт
новый config 0600. SMTP password при отсутствии в старом config остаётся
заглушкой и должен быть получен из действующего закрытого server config, без
копирования в workspace или shell history.

План перехода после подтверждения:

1. Backup старого relay и Snipe-IT.
2. Установить v1.0.0 параллельно, API только на loopback, mail timer выключен.
3. Проверить config, SQLite, локальный mock/staging Snipe-IT и Nginx config.
4. Canary на одном тестовом компьютере и отдельном тестовом событии.
5. Включить новый worker/API, затем NAT/DNS, затем mail timer.
6. Наблюдать retry/dead-letter и watermark; старый relay оставить остановленным,
   но доступным для rollback.
