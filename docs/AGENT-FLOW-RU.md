# Windows-агент: сбор и доставка инвентаризации

Версия агента: `1.0.0`

Основной файл: [`agent/SnipeIT.Inventory.Agent.ps1`](../agent/SnipeIT.Inventory.Agent.ps1)

Пример конфигурации: [`agent/config.example.json`](../agent/config.example.json)

![Принцип работы SnipeIT Inventory Gateway](agent-flow.png)

Схема загружена прямо в репозиторий. Векторная версия:
[`docs/agent-flow.svg`](agent-flow.svg). Она отражает тот же поток, что и код
агента и серверных служб.

## Что уже собрано

Я собрал Windows-агент как самостоятельный PowerShell 5.1-скрипт. Он собирает
данные через стандартные CIM/WMI-классы Windows, формирует одно событие,
шифрует его и пытается доставить Gateway. Snipe-IT API token и SSH key на
компьютер не устанавливаются.

Сейчас агент собирает:

- имя компьютера;
- BIOS serial number, а для пустых/шаблонных значений — hardware UUID;
- производителя и модель;
- текущего пользователя из `Win32_ComputerSystem.UserName` и владельцев
  интерактивных `explorer.exe`-сессий;
- объём RAM;
- список CPU;
- название и версию Windows;
- локальные логические диски: буква, общий и свободный объём;
- версию агента и время наблюдения.

Текущая версия не читает Security Log и не выполняет LDAP sync на компьютере.
Служебные учётки отбрасываются до отправки. Окончательное сопоставление
пользователя выполняет Gateway: назначение происходит только при единственном
точном совпадении username в Snipe-IT. Клиентские признаки увольнения никогда
не могут автоматически сделать check-in актива.

## Как формируется событие

1. Агент читает JSON-конфигурацию и отдельный 256-bit master key в формате
   `base64:`. В конфигурации находятся URL Gateway, `key_id`, пути к ключу и
   SMTP credential, адреса почты, лог, state-файл и путь локальной очереди.
2. CIM/WMI формирует payload `schema_version=1` с типом `inventory`, Unix-time
   поколением `event_generation` и UTC-временем `observed_at`.
3. `event_id` вычисляется как SHA-256 канонического JSON payload без самого
   `event_id`. Поэтому одно событие сохраняет тот же идентификатор во всех
   транспортных каналах.
4. Для каждого события генерируются случайные 32-byte salt и 16-byte IV.
   HKDF-SHA256 выводит из master key два независимых ключа: AES-256 и HMAC.
5. Payload шифруется AES-256-CBC с PKCS#7. HMAC-SHA256 подписывает весь envelope
   до расшифрования. Base64 используется только для переноса бинарных полей.

В открытой части envelope остаются только служебные поля:

```text
version, algorithm, key_id, event_id, sent_at,
salt, iv, ciphertext, hmac_sha256
```

Serial number, имя пользователя, характеристики ПК и диски находятся внутри
`ciphertext` и не появляются в теме или теле письма.

## Канал 1: основной HTTPS

Агент сначала отправляет envelope:

```text
POST https://inventory-gateway.example.com:2443/api/v1/events
Content-Type: application/json
User-Agent: SnipeIT-Inventory-Agent/1.0.0
Timeout: 20 seconds
```

Nginx принимает TLS на порту `2443` и пропускает только точный маршрут
`POST /api/v1/events`. Gateway проверяет размер, схему, `key_id`, HMAC,
допустимое время и канонический `event_id`, после чего кладёт событие в durable
SQLite queue. Повторный HTTPS/SMTP экземпляр с тем же `event_id` становится
дубликатом и второй раз в Snipe-IT не применяется.

## Канал 2: резервный SMTP/IMAP

Если HTTPS завершился исключением или не ответил за 20 секунд, агент отправляет
тот же encrypted envelope через `smtp.example.com:587` с STARTTLS:

```text
From: notification@example.com
To: inventory@example.com
Subject: [SNIPEIT-INVENTORY] RELAY: COMPUTER EVENT_ID_PREFIX
Header: X-SnipeIT-Relay: 1
Attachment: EVENT_ID.snipeit-event.json
```

Установщик хранит SMTP credential в JSON-контейнере, зашифрованном Windows DPAPI
в области `LocalMachine`; файл доступен только `SYSTEM` и локальным
администраторам. Это устраняет привязку `Export-Clixml` к учётке администратора
и позволяет безопасно запускать задачу от `SYSTEM`. Старый CLIXML читается
только для миграционной совместимости. Имя credential обязательно совпадает с
`notification@example.com`. Временный attachment удаляется сразу после
попытки отправки.

На сервере IMAP collector читает `inventory@example.com`, локально декодирует MIME
Subject, проверяет отправителя, заголовок и ровно одно проектное вложение.
Envelope поступает в ту же SQLite queue, что и HTTPS. После inline-обработки
письмо перемещается ровно в один результат:

- `Processed Events` — успех или уже обработанный дубликат;
- `Offline Relay` — временная ошибка, письмо будет повторно проверено;
- `Rejected Events` — окончательная ошибка схемы, подписи или обработки.

Посторонняя почта не перемещается.

## Если не работают оба канала

Если не сработали и HTTPS, и SMTP, агент сохраняет только encrypted envelope в
`QueuePath`. При следующем запуске сначала отправляются старые файлы от самого
раннего к новому, затем текущее событие.

Локальная очередь ограничена:

- срок хранения — 30 дней;
- максимум — 200 событий;
- успешно доставленный файл сразу удаляется;
- за один запуск повторяется не больше 10 старых событий, чтобы не устроить
  SMTP-шквал после массового восстановления связи;
- при первой повторной ошибке проход останавливается, чтобы не создавать шквал.

`StatePath` содержит последний event ID, канал доставки и размер очереди.
`LogPath` хранит локальный технический лог без inventory payload и секретов;
при размере 2 MiB агент оставляет одну предыдущую копию. Lock-файл блокирует
параллельные ручные и плановые запуски.

## Что делает Gateway

1. HTTPS API или IMAP collector проверяет и декодирует envelope.
2. Durable SQLite queue выполняет дедупликацию, lease, retry/backoff и
   dead-letter. Watermark не даёт старому поколению перезаписать новое.
3. Worker ищет asset по serial number, создаёт или обновляет только разрешённые
   поля и применяет server-side mapping custom fields.
4. Только Gateway использует локальный Snipe-IT API token.
5. Dead-letter, восстановление и weekly health отправляются с
   `notification@example.com` на `inventory@example.com`.

Итоговая модель доставки: **at-least-once transport + idempotent server-side
deduplication**. Возможная повторная доставка безопасна благодаря неизменному
`event_id`.

## Запуск на компьютере

Проверка без отправки:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass `
  -File .\SnipeIT.Inventory.Agent.ps1 `
  -Config .\config.json `
  -DryRun
```

Полная установка выполняется из elevated PowerShell. Master key не передаётся
в командной строке; `PSCredential` запрашивается интерактивно или передаётся
системой управления секретами:

```powershell
$smtp = Get-Credential -UserName notification@example.com
.\install-agent.ps1 `
  -SourceDirectory . `
  -ConfigPath .\config.json `
  -MasterKeyPath .\event-master-key.txt `
  -SmtpCredential $smtp
```

Установщик проверяет URL/ключ/JSON, создаёт защищённые каталоги в `ProgramData`,
делает encrypted-event dry-run и регистрирует задачу
`\ExampleOrg\SnipeIT Inventory Collection` от `SYSTEM`: через 2 минуты после
старта, через 5 минут после входа пользователя и ежедневно со случайной
задержкой. Обновление сохраняет предыдущий скрипт для `rollback-agent.ps1`.
`uninstall-agent.ps1` по умолчанию сохраняет config, ключи, логи и очередь;
удаление данных возможно только отдельным `-PurgeData`.
