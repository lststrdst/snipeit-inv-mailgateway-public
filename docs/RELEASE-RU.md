# Как я выпускаю SnipeIT Inventory Gateway v1.0.0

Эта публикация — отдельный публичный snapshot моего production-проекта. Я
оставляю рабочий код и полноценную русскую документацию, но заменяю домены, IP,
почту, имена, OU/DN, закрытые paths и внутренние field IDs на нейтральные
примеры. Я начинаю public history с чистого commit, чтобы удалённые значения не
сохранились в старых Git objects.

Для v1.0.0 я выпускаю wheel, source archive, `SHA256SUMS`, CycloneDX SBOM и
test report. Версию `1.0.0` я использую одинаково в metadata, API response,
User-Agent, systemd descriptions, документации, changelog и release notes. Я
не наследую нумерацию 1.3.x старого агента и не использую 2.0.0.

Перед каждым push я выполняю:

1. unit/integration/security tests и lint;
2. staged secret scan и проверку полной Git history;
3. для public — отдельный anonymization scan;
4. проверку diff и точного remote target;
5. сверку версии и checksums release artifacts.

Публикация `v1.0.0` описывает готовый код, но сама по себе не разрешает
production cutover. Включение systemd units, NAT, DNS и массового агента я
делаю только после полного staging и отдельного явного подтверждения.
