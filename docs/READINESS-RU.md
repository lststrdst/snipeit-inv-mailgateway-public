# Готовность и production-gates

Статус `v1.0.0`: кодовая база готова к ограниченному canary, но production
cutover не выполнен. Никакие службы на `192.0.2.10`, NAT или массовая GPO
раскатка этим репозиторием автоматически не включаются.

## Что уже закрыто

- Windows PowerShell 5.1 agent: сбор hardware/OS/user, шифрование, HTTPS,
  DPAPI-защищённый SMTP fallback, ограниченная локальная очередь, state/log и
  блокировка параллельного запуска.
- Установка от администратора: защищённые ACL, dry-run, задача от `SYSTEM` на
  startup/logon/daily, rollback и безопасный uninstall с сохранением очереди.
- Gateway: узкий HTTPS endpoint, HMAC до расшифрования, clock-skew, device/key
  binding, дедупликация, watermark, lease, retry и dead-letter.
- Snipe-IT: только server-side token, поиск актива по serial, allowlist полей и
  безопасное назначение только по одному точному username.
- SMTP/IMAP: один encrypted attachment, фиксированные адреса и папки,
  inline-обработка, throttled incidents/recovery/weekly report.
- Linux: hardened systemd, Nginx TLS/rate/body limits, backup/restore,
  update/rollback, logrotate и dry-run installer.

## Сеть

| Откуда | Куда | Порт | Режим |
|---|---|---:|---|
| Внутренние ПК | `192.0.2.10` | TCP 2443 | Разрешить firewall, NAT не нужен |
| Internet/пограничный firewall | `192.0.2.10` | TCP 2443 | Пробросить только для ПК вне VPN |
| ПК | `smtp.example.com` | TCP 587 | Исходящий SMTP fallback |
| Gateway | `imap.example.com` | TCP 993 | Исходящий IMAP collector |
| Gateway | `smtp.example.com` | TCP 587 | Исходящие уведомления |
| Nginx | `127.0.0.1` | TCP 8787 | Только localhost, наружу не открывать |
| Worker | локальный Snipe-IT | TCP 443 | Внутри сервера/сети, без WAN NAT |

На endpoints входящие правила не нужны. Порты `8787`, SQLite, IMAP и SMTP не
публикуются. Split DNS: `inventory-gateway.example.com` внутри указывает на
`192.0.2.10`, снаружи — на публичный IP пограничный firewall. Для FQDN нужен сертификат и
проверенный сценарий автоматического продления; открывать TCP 80 не требуется,
если используется DNS-01 или заранее выданный сертификат.

## Что требует фактической инфраструктуры

1. Установить на сервере Nginx и `python3.11-venv`, затем выполнить staging
   install без включения production services.
2. Выпустить TLS-сертификат, настроить internal DNS и межсетевой доступ 2443.
3. Проверить реальные `default_model_id`, `default_status_id` и handles всех
   custom fields в Snipe-IT; значения из example являются заглушками.
4. Убедиться, что пользователи заранее синхронизированы из LDAP в Snipe-IT.
   Gateway намеренно не запускает LDAP sync с endpoints и не хранит SSH key.
5. Создать отдельный ключ для каждого canary-компьютера, связать его через
   `allowed_computers` и проверить процедуру отзыва/ротации.
6. Передать SMTP credential агенту через закрытую endpoint-management систему.
   Не хранить пароль или master keys в SYSVOL, Git, аргументах командной строки
   и общих сетевых папках.
7. Проверить NTP на endpoints и сервере: HTTPS envelope допускает ограниченный
   clock skew; неверные часы дадут authentication failure.
8. Прогнать canary минимум на трёх сценариях: офисный ПК, ноутбук вне офиса и
   ПК с заблокированным HTTPS. Отдельно проверить накопление/доставку очереди,
   duplicate event, смену владельца и rollback.
9. Проверить backup/restore на копии БД и алерт при dead-letter. После этого
   разрешать расширение GPO и, если нужно, WAN NAT.

## Остаточные риски

- SMTP app-password физически присутствует на endpoints в машинно-зашифрованном
  виде. Локальный администратор потенциально может его использовать; пароль
  должен быть отдельным, ротируемым и без лишних прав.
- Без точной Snipe-IT/LDAP синхронизации Gateway обновит железо, но сохранит
  прежнего владельца. Это безопасный отказ, а не автоматическая ошибка checkout.
- Общий fleet key ослабляет изоляцию endpoints. Production config поэтому
  запрещает ingest keys без `allowed_computers`; рекомендуются per-device keys.
- Внешний TCP 2443 увеличивает attack surface. Его следует включать только
  после canary, с Nginx limits, валидным TLS и мониторингом 401/413/429.
- Ни один тест не заменяет фактическую проверку антивируса, proxy и GPO на
  корпоративном Windows image.

Production-ready означает: все девять инфраструктурных пунктов подтверждены,
canary стабилен не менее нескольких плановых циклов, а rollback проверен до
массовой раскатки.
