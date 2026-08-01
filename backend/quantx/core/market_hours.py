"""Market hours and session utilities for NSE/BSE."""

from __future__ import annotations

from datetime import datetime, time, timedelta
from typing import Optional
from zoneinfo import ZoneInfo

from quantx.core.config import Settings, get_settings
from quantx.core.models import MarketSession

IST = ZoneInfo("Asia/Kolkata")


def _parse_hhmm(value: str) -> time:
    h, m = value.split(":")
    return time(int(h), int(m))


class MarketClock:
    def __init__(self, settings: Optional[Settings] = None):
        self.settings = settings or get_settings()
        hours = self.settings.markets.trading_hours
        self.open_t = _parse_hhmm(hours.open)
        self.close_t = _parse_hhmm(hours.close)
        self.pre_t = _parse_hhmm(self.settings.markets.pre_market)
        self.post_t = _parse_hhmm(self.settings.markets.post_market)

    def now_ist(self) -> datetime:
        return datetime.now(IST)

    def session(self, now: Optional[datetime] = None) -> MarketSession:
        now = now or self.now_ist()
        if now.tzinfo is None:
            now = now.replace(tzinfo=IST)
        else:
            now = now.astimezone(IST)

        t = now.time()
        weekday = now.weekday()  # 0=Mon ... 6=Sun

        if weekday >= 5:
            return MarketSession(
                is_open=False,
                phase="closed",
                server_time_ist=now.strftime("%Y-%m-%d %H:%M:%S IST"),
                next_open=self._next_open(now).strftime("%Y-%m-%d %H:%M IST"),
            )

        if self.open_t <= t <= self.close_t:
            phase = "open"
            is_open = True
        elif self.pre_t <= t < self.open_t:
            phase = "pre_market"
            is_open = False
        elif self.close_t < t <= self.post_t:
            phase = "post_market"
            is_open = False
        else:
            phase = "closed"
            is_open = False

        return MarketSession(
            is_open=is_open,
            phase=phase,
            server_time_ist=now.strftime("%Y-%m-%d %H:%M:%S IST"),
            next_open=None if is_open else self._next_open(now).strftime("%Y-%m-%d %H:%M IST"),
        )

    def _next_open(self, now: datetime) -> datetime:
        candidate = now.replace(
            hour=self.open_t.hour, minute=self.open_t.minute, second=0, microsecond=0
        )
        if now.time() >= self.open_t or now.weekday() >= 5:
            candidate += timedelta(days=1)
        while candidate.weekday() >= 5:
            candidate += timedelta(days=1)
        return candidate

    def validate_trading_allowed(self, allow_paper_anytime: bool = True, mode: str = "paper") -> tuple[bool, str]:
        """Live mode must trade only in market hours. Paper can analyze anytime."""
        session = self.session()
        if mode == "paper" and allow_paper_anytime:
            return True, f"Paper mode — analysis allowed ({session.phase})"
        if session.is_open:
            return True, "Market open"
        return False, f"Market {session.phase} — live trading blocked until session open"
