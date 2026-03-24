from datetime import datetime
from zoneinfo import ZoneInfo

from core.session_guard import SessionGuard

IST = ZoneInfo("Asia/Kolkata")


def test_entry_blocked_during_opening_window():
    guard = SessionGuard()

    assert guard.is_entry_allowed(datetime(2026, 3, 23, 9, 20, tzinfo=IST)) is False
    assert guard.active_block_reason(datetime(2026, 3, 23, 9, 20, tzinfo=IST)) is not None


def test_entry_blocked_during_closing_window():
    guard = SessionGuard()

    assert guard.is_entry_allowed(datetime(2026, 3, 23, 15, 20, tzinfo=IST)) is False
    assert guard.active_block_reason(datetime(2026, 3, 23, 15, 20, tzinfo=IST)) is not None


def test_exits_remain_allowed_during_blocked_windows():
    guard = SessionGuard()

    assert guard.is_exit_allowed(datetime(2026, 3, 23, 9, 20, tzinfo=IST)) is True
    assert guard.is_exit_allowed(datetime(2026, 3, 23, 15, 20, tzinfo=IST)) is True


def test_entries_allowed_outside_blocked_windows():
    guard = SessionGuard()

    assert guard.is_entry_allowed(datetime(2026, 3, 23, 9, 30, tzinfo=IST)) is True
    assert guard.is_entry_allowed(datetime(2026, 3, 23, 14, 0, tzinfo=IST)) is True
