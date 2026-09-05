import datetime as dt

from snipeit_inventory_gateway.reports import build_inventory_rows, weekly_report


def test_weekly_report_is_russian_branded_and_has_top_statistics(config):
    config.snipeit.custom_field_map.update(
        {
            "last_success": "_snipeit_example_success_1",
            "agent_version": "_snipeit_example_agent_2",
            "last_error": "_snipeit_example_error_3",
        }
    )
    current = dt.datetime(2026, 9, 1, 9, tzinfo=dt.UTC)
    assets = [
        {
            "name": "LAPTOP-001",
            "serial": "SERIAL-001",
            "category": {"id": 1},
            "assigned_to": {"name": "Тестовый пользователь"},
            "created_at": {"datetime": "2026-01-01T00:00:00+00:00"},
            "custom_fields": {
                "_snipeit_example_success_1": {"value": "2026-08-31T09:00:00+00:00"},
                "_snipeit_example_agent_2": {"value": "1.0.0"},
                "_snipeit_example_error_3": {"value": ""},
            },
        },
        {
            "name": "LAPTOP-002",
            "serial": "SERIAL-002",
            "category": {"id": 1},
            "assigned_to": None,
            "created_at": {"datetime": "2026-01-01T00:00:00+00:00"},
            "custom_fields": {},
        },
    ]
    rows = build_inventory_rows(assets, config, current)
    subject, text, html = weekly_report(rows, config, current, logo=True)
    assert subject == "[SnipeIT Inventory Gateway][WEEKLY] 2026-W36 · критично 1 из 2"
    assert "Всего компьютеров" in html and "Актуальные" in html and "Критические" in html
    assert "Нет данных" in html and "cid:inventory-logo" in html
    assert "#FE223C" in html and "Компьютер" in html
    assert "Total:" not in text and "Weekly Report" not in html
