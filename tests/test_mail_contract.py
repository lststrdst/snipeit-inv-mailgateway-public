import pytest

from snipeit_inventory_gateway.mail_contract import mail_subject, subject_matches_category


@pytest.mark.parametrize(
    ("category", "tag"),
    [
        ("relay", "RELAY"),
        ("computer-report", "REPORT"),
        ("weekly-report", "WEEKLY"),
        ("owner-change", "OWNER CHANGE"),
        ("error", "ERROR"),
        ("recovery", "RECOVERY"),
    ],
)
def test_subject_taxonomy_is_stable(category, tag):
    subject = mail_subject(category, "LAPTOP-107 · результат")
    assert subject.startswith(f"[SnipeIT Inventory Gateway][{tag}] ")


def test_subject_is_one_line_and_category_is_checked():
    subject = mail_subject("error", "IMAP\r\n Bcc: personal@example.com")
    assert "\r" not in subject and "\n" not in subject
    assert subject_matches_category(subject, "error")
    assert not subject_matches_category(subject, "weekly-report")
