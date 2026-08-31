# SnipeIT Inventory Gateway v1.0.0

Это публичная обезличенная редакция моего production-проекта. Я оставил здесь
рабочий код, тесты, installer/update/rollback и полную русскую документацию, но
заменил реальные домены, IP, адреса почты, имена, task paths и Snipe-IT field
handles на значения `example.com`, RFC 5737 и нейтральные идентификаторы. Перед
использованием нужно создать собственный закрытый config и подставить параметры
своей инфраструктуры; примерные значения не предназначены для production.

Я разработал эту систему, потому что мне была нужна инвентаризация, не
привязанная к доступности локальной сети. Обычный агент, который пишет прямо в
Snipe-IT, перестаёт быть полезным, как только ноутбук уезжает из офиса или
теряет VPN. Хранить Snipe-IT API token и SSH key на каждом Windows-компьютере я
тоже не хотел: компрометация одного endpoint сразу открыла бы доступ к
внутренней системе учёта.

Поэтому я вынес доверенную часть в отдельный Gateway. Windows-агент собирает
инвентаризацию и сначала отправляет зашифрованное событие через
`HTTPS POST /api/v1/events`. Если HTTPS временно недоступен, агент отправляет
то же событие письмом с читаемой темой и зашифрованным вложением. Если нет и
почты, encrypted envelope остаётся в ограниченной локальной очереди и
повторяется позднее. Только Gateway находится рядом со Snipe-IT и использует
его локальный API.

## Что я реализовал

- узкий HTTPS endpoint без публичного доступа к Snipe-IT API;
- резервную доставку SMTP/IMAP без plaintext inventory во вложении;
- единую durable SQLite queue для HTTPS и почтового ingest;
- дедупликацию по `event_id`, поколения, watermark/stale protection, leases,
  retry и dead-letter;
- AES-256-CBC Encrypt-then-MAC с отдельным HMAC-SHA256 key, случайными salt/IV,
  `key_id` и ротацией;
- Windows installer, SYSTEM scheduled task, machine-DPAPI для SMTP credential,
  локальные state/log/queue, rollback и uninstall;
- IMAP-классификатор, который локально декодирует MIME Subject, распознаёт
  только проектные письма и не трогает unrelated mail;
- server-side ownership policy: endpoint не может назначить служебную учётную
  запись владельцем или сам объявить пользователя уволенным;
- systemd hardening, Nginx limits, logrotate, backup/restore, update/rollback,
  healthcheck и миграцию с 1.3.3.

## Почему я выбрал Gateway

Я хотел оставить агент простым и однонаправленным: ему нужны только исходящие
соединения TCP `2443` и, для fallback, TCP `587`. Входящие порты на Windows ПК
не требуются. Snipe-IT token остаётся в закрытом server config, а запись в
Snipe-IT выполняет один контролируемый worker. Это дало мне единое место для
валидации схемы, защиты от старых событий, повторов, уведомлений и аудита.

## Безопасность

Я использую случайный 256-bit master key, а не человеческий пароль. Через HKDF
я вывожу независимые encryption и MAC keys. HMAC проверяется до расшифрования;
Base64 служит только транспортной кодировкой. В HTTPS ingest я ограничил
маршрут, размер тела, частоту и число соединений. В почтовом ingest я проверяю
отправителя, тему, служебный заголовок, тип и количество вложений, размер,
схему, `key_id`, HMAC и `event_id`.

Production secrets я не храню в Git, примерах, тестах, release artifacts или
логах. Перед каждым push я запускаю secret scan; для публичного mirror я также
запускаю отдельную проверку анонимизации.

## Документация и эксплуатация

Я описал компоненты и порядок работы в следующих документах:

- [архитектура](docs/ARCHITECTURE-RU.md);
- [путь события от Windows-агента](docs/AGENT-FLOW-RU.md);
- [операционный runbook](docs/OPERATIONS-RU.md);
- [миграция с 1.3.3](docs/MIGRATION-1.3.3-RU.md);
- [готовность к пилоту и production gates](docs/READINESS-RU.md);
- [план релиза v1.0.0](docs/RELEASE-RU.md).

Обезличенную схему потока я храню рядом с кодом в формате
[SVG](docs/agent-flow.svg).

## Ограничения v1.0.0

Я считаю `main` release candidate до завершения полного server staging. Перед
production мне ещё нужно проверить реальные Snipe-IT field handles/IDs,
поведение IMAP/SMTP, TLS certificate, split DNS, NAT и rollback drill. Сам
production cutover я не выполняю без отдельного явного подтверждения.
