import datetime as dt

from snipeit_inventory_gateway.queue import EventQueue


def test_owner_requires_three_distinct_events_across_24_hours(tmp_path):
    queue = EventQueue(tmp_path / "events.sqlite3")
    start = dt.datetime(2026, 9, 1, 8, tzinfo=dt.UTC)
    first = queue.decide_owner("PC-1", "d.user", start.isoformat(), "event-1", 3, 24, 7)
    second = queue.decide_owner(
        "PC-1", "d.user", (start + dt.timedelta(hours=12)).isoformat(), "event-2", 3, 24, 7
    )
    too_early = queue.decide_owner(
        "PC-1", "d.user", (start + dt.timedelta(hours=23)).isoformat(), "event-3", 3, 24, 7
    )
    confirmed = queue.decide_owner(
        "PC-1", "d.user", (start + dt.timedelta(hours=25)).isoformat(), "event-4", 3, 24, 7
    )
    assert first.username is None and second.username is None and too_early.username is None
    assert confirmed.username == "d.user" and confirmed.state == "confirmed_new"
    queue.close()


def test_empty_and_alternating_users_never_remove_or_jump_owner(tmp_path):
    queue = EventQueue(tmp_path / "events.sqlite3")
    start = dt.datetime(2026, 9, 1, 8, tzinfo=dt.UTC)
    for index, username in enumerate(("d.one", "d.two", "d.one", "d.two"), 1):
        decision = queue.decide_owner(
            "PC-1",
            username,
            (start + dt.timedelta(days=index)).isoformat(),
            f"event-{index}",
            3,
            24,
            7,
        )
        assert decision.username is None
    missing = queue.decide_owner("PC-1", None, start.isoformat(), "empty", 3, 24, 7)
    assert missing.username is None and missing.state == "preserve_missing"
    queue.close()


def test_duplicate_event_does_not_count_as_confirmation(tmp_path):
    queue = EventQueue(tmp_path / "events.sqlite3")
    start = dt.datetime(2026, 9, 1, 8, tzinfo=dt.UTC)
    queue.decide_owner("PC-1", "d.user", start.isoformat(), "same", 2, 1, 7)
    duplicate = queue.decide_owner(
        "PC-1", "d.user", (start + dt.timedelta(hours=2)).isoformat(), "same", 2, 1, 7
    )
    assert duplicate.username is None and duplicate.confirmations == 1
    queue.close()


def test_confirmed_owner_observation_cancels_another_pending_candidate(tmp_path):
    queue = EventQueue(tmp_path / "events.sqlite3")
    start = dt.datetime(2026, 9, 1, 8, tzinfo=dt.UTC)
    queue.decide_owner("PC-1", "d.one", start.isoformat(), "a1", 2, 1, 7)
    assert queue.decide_owner(
        "PC-1", "d.one", (start + dt.timedelta(hours=2)).isoformat(), "a2", 2, 1, 7
    ).username == "d.one"
    assert queue.decide_owner(
        "PC-1", "d.two", (start + dt.timedelta(days=1)).isoformat(), "b1", 2, 1, 7
    ).username is None
    assert queue.decide_owner(
        "PC-1", "d.one", (start + dt.timedelta(days=2)).isoformat(), "a3", 2, 1, 7
    ).username == "d.one"
    interrupted = queue.decide_owner(
        "PC-1", "d.two", (start + dt.timedelta(days=3)).isoformat(), "b2", 2, 1, 7
    )
    assert interrupted.username is None and interrupted.confirmations == 1
    queue.close()
