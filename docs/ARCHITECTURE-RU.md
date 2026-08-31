# Архитектура SnipeIT Inventory Gateway v1.0.0

Подробный поток Windows-агента и доставки показан в
[`AGENT-FLOW-RU.md`](AGENT-FLOW-RU.md) и на
[`визуальной SVG-схеме`](agent-flow.svg).

## Граница доверия

Windows Agent знает только URL Gateway, `key_id`, ключ шифрования событий и
учётные данные резервного SMTP, защищённые ACL/DPAPI. На нём отсутствуют
Snipe-IT API token и SSH key. Snipe-IT token существует только в закрытом
server config с правами `0640 root:snipeit-inventory-gateway`.

В production каждый ingest key обязан иметь `allowed_computers`. Рекомендуемый
режим — отдельный `key_id` и master key на компьютер. Поэтому компрометация
одного endpoint не позволяет подписать событие от имени другого компьютера.

```text
Windows Agent ── HTTPS:2443 ──┐
                              ├─> durable SQLite queue ─> worker ─> local Snipe-IT API
Windows Agent ── SMTP ─> IMAP ┘
```

Если оба транспорта недоступны, агент сохраняет encrypted envelope локально
с тем же event_id, повторяет старые события перед новым запуском и ограничивает
очередь 30 днями/200 файлами. В локальной очереди нет plaintext inventory.

Nginx принимает только `POST /api/v1/events`; остальные URI получают 404.
пограничный firewall публикует WAN:2443 → 192.0.2.10:2443. Snipe-IT/Apache продолжает
слушать 443. Split DNS для `inventory-gateway.example.com`: внутри
`192.0.2.10`, снаружи — публичный адрес пограничный firewall.

## Криптопротокол

Envelope v1 использует случайные 32-byte salt и 16-byte IV для каждого
события. Из 256-bit случайного master key через HKDF-SHA256 выводятся два
независимых ключа: AES-256-CBC и HMAC-SHA256. HMAC вычисляется по всему
каноническому envelope до расшифрования (Encrypt-then-MAC). Base64 — только
транспортная кодировка. `key_id` позволяет держать текущий и decrypt-only
ключи во время ротации.

`decrypt_only` не принимается для нового HTTPS ingest, но продолжает принимать
старые события через offline SMTP-очередь. Это даёт ротацию без потери
накопленных событий и без возможности использовать отозванный ключ как
активный.

`event_id` — SHA-256 канонического payload без самого `event_id`. SQLite
обеспечивает дедупликацию между HTTPS и SMTP. Watermark на компьютер хранит
последние generation/observed_at и не даёт старому событию перезаписать новое.

## Состояния очереди

`pending → processing → processed` — успех. Временная ошибка даёт
`retry` с exponential backoff, затем `dead_letter`. Ошибка данных/API-контракта
даёт `rejected`. Событие ниже watermark получает `stale` без записи в Snipe-IT.
Lease позволяет подобрать событие после падения worker.

Новый asset создаётся по serial без записи `asset_tag`; последующие события
обновляют только allowlisted поля. Логические custom fields переводятся в
server-side Snipe-IT handles через строгий `custom_field_map`.

Gateway нормализует обнаруженный username, отбрасывает системные/служебные
учётки и ищет ровно одно точное совпадение в Snipe-IT. Только после этого
выполняется checkout. Endpoint не считается источником истины для disabled или
offboarding-состояния и не может инициировать check-in; вывод актива в запас
должен запускаться отдельным серверным процессом на основании каталога.

## Почта

Collector декодирует MIME Subject локально. Приоритет классификации строго:
relay → weekly → error → warning → alert → report. Письмо попадает ровно в одну
папку, а unrelated остаётся в исходной папке.

```text
SnipeIT Inventory/
  ! Weekly Reports
  Alerts
  Errors
  Offline Relay
  Processed Events
  Rejected Events
  Reports
  Warnings
```

Новый relay требует разрешённого From, точного subject prefix,
`X-SnipeIT-Relay: 1` и ровно одного `.snipeit-event.json`. После ingest событие
обрабатывается inline в том же проходе: успех → Processed, временная ошибка →
Offline Relay, окончательная → Rejected. Совместимые темы:
`[SNIPEIT-RELAY]`, `[PCINV-REPORT]`, `[PCINV-ALERT]`, `PC Inventory ...`.

## Наблюдаемость

Dead-letter уведомляется немедленно. Повторяющиеся ошибки IMAP/очереди
throttle-ятся, восстановление отправляет отдельное письмо. Недельный health
report запускается systemd timer. Исходящие сообщения: только
`notification@example.com` → `inventory@example.com`, `smtp.example.com:587`, STARTTLS.
