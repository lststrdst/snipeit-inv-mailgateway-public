# Архитектура SnipeIT Inventory Gateway v1.0.0

Подробный поток Windows-агента и доставки показан в
[`AGENT-FLOW-RU.md`](AGENT-FLOW-RU.md), на
[визуальной SVG-схеме](mail-delivery-figma.svg) и в
[редактируемом оригинале Figma/FigJam](https://www.figma.com/board/lC8wdiuqwkBqjs2yGzhR2x).

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
UserGate публикует WAN:2443 → 192.0.2.202:2443. Snipe-IT/Apache продолжает
слушать 443. Split DNS для `inventory-gateway.example.com`: внутри
`192.0.2.202`, снаружи — публичный адрес UserGate.

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

Gateway нормализует обнаруженный username и принимает только стандартный
формат `инициал.фамилия` либо исключение из закрытого config. Новый владелец
должен повториться в трёх разных событиях минимум за 24 часа. Другое имя
сбрасывает кандидата, а пустое имя всегда означает `preserve`. После
подтверждения Gateway ищет ровно одно точное совпадение в Snipe-IT и только
тогда выполняет checkout. Подробный автомат описан в
[OWNERSHIP-SAFETY-RU.md](OWNERSHIP-SAFETY-RU.md).

## Почта

Collector декодирует MIME Subject локально и проверяет служебную категорию,
класс письма, отдельные SMTP/IMAP identities и единственного получателя. Relay
всегда имеет высший приоритет. Письмо попадает ровно в одну папку, а unrelated
остаётся в исходной папке.

```text
SnipeIT Inventory/
  Reports
  Weekly Reports
  Errors
```

Новый relay требует точных From/To, метки `[RELAY]`, заголовков
`X-SnipeIT-Relay: 1` и `X-SnipeIT-Mail-Class: transport`, а также ровно одного
`.snipeit-event.json`. После ingest событие обрабатывается inline в том же
проходе: успех → `Reports`, временная или окончательная ошибка → `Errors` с
отдельным читаемым error report. Retry/dead-letter хранит SQLite, а не IMAP.
Старые зашифрованные relay-письма продолжают обрабатываться; старые отчёты
перемещаются в Trash. Полные правила описаны в
[MAIL-REPORTS-RU.md](MAIL-REPORTS-RU.md).

## Наблюдаемость

Dead-letter уведомляется немедленно. Повторяющиеся ошибки IMAP/очереди
throttle-ятся, восстановление отправляет отдельное письмо. Брендированный
недельный отчёт по всем компьютерам запускается systemd timer. Исходящие сообщения: только
`notification@example.com` → `inventory@example.com`, `smtp.example.com:587`, STARTTLS.
