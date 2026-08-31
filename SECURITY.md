# Security policy

Не открывайте issue с секретом или production payload. Немедленно ротируйте
скомпрометированные Snipe-IT/SMTP/IMAP/event keys. В логах допустимы event_id,
computer_name, status и тип ошибки; payload, token, passwords, ciphertext keys и
полные HTTP bodies запрещены. Все изменения криптопротокола требуют security
review и обратимых migration tests.
