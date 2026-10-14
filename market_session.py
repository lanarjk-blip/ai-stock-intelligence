"""IDX regular-market session guard for live/actionable signals.

Times are WIB (Asia/Jakarta). Defaults follow the regular equity-market schedule:
Mon-Thu 09:00-12:00 and 13:30-15:50; Fri 09:00-11:30 and 14:00-15:50.

Holiday handling is centralized here.  Bundled holidays come from the verified
IDX/KSEI calendar in data/idx_holidays.json and are unioned with optional
config ``market_holidays`` overrides.  Pre-Market and live-session logic must
use this module rather than maintaining independent holiday lists.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, time, timezone, timedelta
from functools import lru_cache
from zoneinfo import ZoneInfo

WIB = ZoneInfo("Asia/Jakarta")
_BASE = os.path.dirname(os.path.abspath(__file__))
_CALENDAR_PATH = os.path.join(_BASE, "data", "idx_holidays.json")


@lru_cache(maxsize=1)
def _bundled_calendar():
    try:
        with open(_CALENDAR_PATH, "r", encoding="utf-8") as fh:
            payload = json.load(fh) or {}
        holidays = set()
        for values in (payload.get("holidays") or {}).values():
            for value in values or []:
                holidays.add(str(value))
        years = {int(x) for x in (payload.get("verified_years") or [])}
        return {
            "holidays": holidays,
            "verified_years": years,
            "source": str(payload.get("source") or ""),
        }
    except Exception:
        return {"holidays": set(), "verified_years": set(), "source": ""}


def market_holidays(config=None):
    cfg = config or {}
    holidays = set(_bundled_calendar()["holidays"])
    holidays.update(str(x) for x in (cfg.get("market_holidays") or []))
    return holidays


def calendar_metadata(day=None):
    local_day = day or datetime.now(timezone.utc).astimezone(WIB).date()
    bundled = _bundled_calendar()
    return {
        "year": int(local_day.year),
        "verified": int(local_day.year) in bundled["verified_years"],
        "verified_years": sorted(bundled["verified_years"]),
        "source": bundled["source"],
    }


def _parse_hhmm(value, fallback):
    try:
        hh, mm = str(value).strip().split(":", 1)
        return time(int(hh), int(mm))
    except Exception:
        hh, mm = fallback.split(":", 1)
        return time(int(hh), int(mm))


def _local(now=None):
    local = now or datetime.now(timezone.utc).astimezone(WIB)
    if local.tzinfo is None:
        return local.replace(tzinfo=WIB)
    return local.astimezone(WIB)


def is_trading_date(day, config=None):
    return day.weekday() < 5 and day.isoformat() not in market_holidays(config)


def _is_trading_date(day, config=None):
    # Compatibility alias for earlier callers/tests.
    return is_trading_date(day, config)


def next_trading_date(config=None, now=None, after_close=True):
    """Return the market date a new Pre-Market snapshot should target."""
    cfg = config or {}
    local = _local(now)
    day = local.date()
    if is_trading_date(day, cfg):
        if day.weekday() == 4:
            final_close = _parse_hhmm(cfg.get("idx_fri_session2_close"), "15:50")
        else:
            final_close = _parse_hhmm(cfg.get("idx_session2_close"), "15:50")
        if not after_close or local.time().replace(tzinfo=None) < final_close:
            return day.isoformat()
        day += timedelta(days=1)
    else:
        day += timedelta(days=1)
    while not is_trading_date(day, cfg):
        day += timedelta(days=1)
    return day.isoformat()


def session_status(config=None, now=None):
    cfg = config or {}
    local = _local(now)

    day_text = local.date().isoformat()
    weekday = local.weekday()
    holidays = market_holidays(cfg)
    calendar = calendar_metadata(local.date())
    common = {
        "wib": local.isoformat(),
        "market_date": day_text,
        "calendar_verified": calendar["verified"],
        "calendar_year": calendar["year"],
    }

    if weekday >= 5:
        return {"live": False, "status": "WEEKEND", **common}
    if day_text in holidays:
        return {"live": False, "status": "HOLIDAY", **common}

    if weekday == 4:
        sessions = [
            (_parse_hhmm(cfg.get("idx_fri_session1_open"), "09:00"), _parse_hhmm(cfg.get("idx_fri_session1_close"), "11:30"), "SESSION_1"),
            (_parse_hhmm(cfg.get("idx_fri_session2_open"), "14:00"), _parse_hhmm(cfg.get("idx_fri_session2_close"), "15:50"), "SESSION_2"),
        ]
    else:
        sessions = [
            (_parse_hhmm(cfg.get("idx_session1_open"), "09:00"), _parse_hhmm(cfg.get("idx_session1_close"), "12:00"), "SESSION_1"),
            (_parse_hhmm(cfg.get("idx_session2_open"), "13:30"), _parse_hhmm(cfg.get("idx_session2_close"), "15:50"), "SESSION_2"),
        ]

    current = local.time().replace(tzinfo=None)
    for start, end, name in sessions:
        if start <= current < end:
            return {"live": True, "status": name, **common}

    if current < sessions[0][0]:
        status = "PRE_MARKET"
    elif sessions[0][1] <= current < sessions[1][0]:
        status = "BREAK"
    else:
        status = "CLOSED"
    return {"live": False, "status": status, **common}


def is_live_market_session(config=None, now=None):
    return bool(session_status(config=config, now=now).get("live"))
