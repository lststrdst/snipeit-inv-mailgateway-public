# Как я выпускаю SnipeIT Inventory Gateway v1.0.0

Я веду полный приватный production-репозиторий и отдельный публичный snapshot.
В приватном репозитории я храню production-specific документацию и историю
изменений. В публичном я оставляю рабочий код и полноценную русскую
документацию, но заменяю домены, IP, почту, имена, OU/DN, закрытые paths и
внутренние field IDs на нейтральные примеры. Public history я начинаю с
чистого commit, чтобы удалённые значения не сохранились в старых Git objects.

Для v1.0.0 проект собирает wheel и source archive. Перед созданием Git tag я
сохраню test report и SHA-256 checksums артефактов. Версию `1.0.0` я использую
одинаково в metadata, API response, User-Agent, systemd descriptions,
документации, changelog и release notes. Я не наследую нумерацию 1.3.x старого
агента и не использую 2.0.0.

Перед каждым push я выполняю:

1. unit/integration/security tests и lint;
2. staged secret scan и проверку полной Git history;
3. для public — отдельная проверка, что во внешнюю ветку не попали внутренние
   домены, адреса, учетные записи и секреты;
4. проверку diff и точного remote target;
5. сверку версии и checksums release artifacts.

Публикация `v1.0.0` описывает готовый код, но сама по себе не разрешает
production cutover. Включение systemd units, NAT, DNS и массового агента я
делаю только после полного staging и отдельного явного подтверждения.
