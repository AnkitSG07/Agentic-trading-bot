from dataclasses import dataclass, field
from datetime import datetime, time
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")


@dataclass(frozen=True, slots=True)
class SessionBlockWindow:
    start: time
    end: time
    reason: str

    def contains(self, current_time: time) -> bool:
        return self.start <= current_time < self.end


@dataclass(frozen=True, slots=True)
class SessionGuardConfig:
    timezone: ZoneInfo = IST
    exits_allowed_during_entry_blocks: bool = True
    entry_block_windows: tuple[SessionBlockWindow, ...] = field(
        default_factory=lambda: (
            SessionBlockWindow(time(9, 15), time(9, 30), "Opening range entry block"),
            SessionBlockWindow(time(15, 15), time(15, 30), "Closing auction entry block"),
        )
    )


class SessionGuard:
    def __init__(self, config: SessionGuardConfig | None = None) -> None:
        self.config = config or SessionGuardConfig()

    def _normalize_now(self, now: datetime | None = None) -> datetime:
        current = now or datetime.now(tz=self.config.timezone)
        if current.tzinfo is None:
            return current.replace(tzinfo=self.config.timezone)
        return current.astimezone(self.config.timezone)

    def active_block_reason(self, now: datetime | None = None) -> str | None:
        current = self._normalize_now(now)
        current_time = current.timetz().replace(tzinfo=None)
        for window in self.config.entry_block_windows:
            if window.contains(current_time):
                return window.reason
        return None

    def is_entry_allowed(self, now: datetime | None = None) -> bool:
        return self.active_block_reason(now) is None

    def is_exit_allowed(self, now: datetime | None = None) -> bool:
        return bool(self.config.exits_allowed_during_entry_blocks)
