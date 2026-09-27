from datetime import UTC, datetime, timedelta


class SystemClock:
    def now(self) -> datetime:
        return datetime.now(UTC)


class OffsetClock:
    """The system time plus an offset the dev route moves forward, to demo the Nudges and
    the deadline without waiting."""

    def __init__(self) -> None:
        self._offset = timedelta()

    def now(self) -> datetime:
        return datetime.now(UTC) + self._offset

    def advance(self, delta: timedelta) -> None:
        self._offset += delta


class FixedClock:
    """A clock for tests: time only moves when told to."""

    def __init__(self, now: datetime) -> None:
        self._now = now

    def now(self) -> datetime:
        return self._now

    def set(self, now: datetime) -> None:
        self._now = now
