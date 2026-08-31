# Эксплуатация

## Локальная проверка

```bash
python3 -m venv .venv
.venv/bin/pip install -e '.[dev]'
.venv/bin/pytest --cov=snipeit_inventory_gateway
.venv/bin/ruff check .
.venv/bin/detect-secrets scan
.venv/bin/pip-audit --progress-spinner off
```

`config.example.json` не является рабочим конфигом. Скопируйте его вне Git,
задайте права 0640 и замените все `REPLACE_*`. Master key создаётся только
криптографическим RNG:

```bash
scripts/generate-master-key.sh /etc/snipeit-inventory-gateway/master-key.txt
```

Скрипт не печатает ключ. Значение переносится в закрытый config через
защищённый интерактивный канал, не через аргумент командной строки.

## Staging/dry-run

```bash
python -m build
sudo scripts/install.sh --dry-run \
  --artifact dist/snipeit_inventory_gateway-1.0.0-py3-none-any.whl \
  --config /etc/snipeit-inventory-gateway/config.json
scripts/migrate-1.3.3.py --old-config /protected/old/config.json \
  --output /etc/snipeit-inventory-gateway/config.json --dry-run
```

Dry-run создаёт только временное venv, не переключает symlink, systemd или
Nginx и не читает содержимое событий из production IMAP. Production cutover
запрещён до явного подтверждения владельца.

## Установка и переключение

После подтверждения: установить unit/nginx/logrotate, проверить `nginx -t`,
включить API/worker/timers и отдельно настроить пограничный firewall NAT/DNS. Installer сам
не включает и не запускает сервисы.

```bash
sudo scripts/install.sh --artifact dist/*.whl --config /root/protected-config.json
sudo install -m 0644 deploy/systemd/* /etc/systemd/system/
sudo install -m 0644 deploy/logrotate/snipeit-inventory-gateway /etc/logrotate.d/
sudo install -m 0644 deploy/nginx/snipeit-inventory-gateway-limits.conf /etc/nginx/conf.d/
sudo install -m 0644 deploy/nginx/snipeit-inventory-gateway.conf /etc/nginx/sites-available/
sudo nginx -t
```

## Health, backup, restore, rollback

```bash
sudo -u snipeit-inventory-gateway /opt/snipeit-inventory-gateway/current/venv/bin/snipeit-inventory-gateway health
sudo scripts/backup.sh
sudo scripts/restore.sh /var/backups/snipeit-inventory-gateway/ARCHIVE.tar.gz
sudo scripts/rollback.sh
```

Backup использует SQLite online backup API, сохраняет config и БД в архиве
0600 и добавляет SHA-256. Restore останавливает writers, проверяет checksum и
права. Rollback атомарно возвращает previous release symlink.

## Ротация ключа

1. Сгенерировать новый master key и новый уникальный `key_id`; в production
   заполнить `allowed_computers`.
2. Добавить новый key в Gateway; старый отметить `decrypt_only: true`.
3. Распространить новый key/key_id на агент через защищённый GPO канал.
4. После максимального срока offline-очереди удалить старый key из Gateway.
5. Никогда не переиспользовать `key_id` с другим master key.

## Windows-агент и GPO

Ключ создаётся без вывода значения в терминал:

```powershell
.\agent\new-agent-master-key.ps1 -Path C:\Protected\LAPTOP-001.key
$smtp = Get-Credential -UserName notification@example.com
.\agent\install-agent.ps1 `
  -SourceDirectory .\agent `
  -ConfigPath C:\Protected\LAPTOP-001.json `
  -MasterKeyPath C:\Protected\LAPTOP-001.key `
  -SmtpCredential $smtp
```

До раскатки тот же `key_id`/master key добавляется в закрытый server config с
`allowed_computers: ["LAPTOP-001"]`. Для пилота без почтового fallback можно
явно использовать `-NoSmtpFallback`; недоставленные события останутся в
зашифрованной локальной очереди.

Для GPO installer запускается startup-script от `SYSTEM`. Не помещайте пароль
SMTP, master key или server config в SYSVOL в открытом виде. Используйте
закрытый канал endpoint-management/секрет-хранилище. После установки проверьте
Last Run Result задачи, `State\state.json` и отсутствие plaintext inventory в
`Queue`/`Logs`.
