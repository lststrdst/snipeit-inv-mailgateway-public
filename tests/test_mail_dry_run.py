from snipeit_inventory_gateway import mail


class FakeImap:
    def __enter__(self):
        return self

    def __exit__(self, *_):
        return None

    def login(self, *_):
        return "OK", []

    def create(self, *_):
        return "OK", []

    def select(self, *_):
        return "OK", []

    def uid(self, command, *_):
        assert command == "SEARCH"
        return "OK", [b""]


def test_mail_dry_run_never_opens_queue_or_snipe(config, monkeypatch):
    monkeypatch.setattr(mail.imaplib, "IMAP4_SSL", lambda *_: FakeImap())
    monkeypatch.setattr(mail, "EventQueue", lambda *_: (_ for _ in ()).throw(AssertionError()))
    monkeypatch.setattr(mail, "SnipeITClient", lambda *_: (_ for _ in ()).throw(AssertionError()))
    assert mail.collect(config, dry_run=True) == {}
