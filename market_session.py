"""IDX regular-market session guard for live/actionable signals.

Times are WIB (Asia/Jakarta). Defaults follow the regular equity market schedule:
Mon-Thu 09:00-12:00 and 13:30-15:50; Fri 09:00-11:30 and 14:00-15:50.
Optional config keys can override times and provide market_holidays (YYYY-MM-DD list).
"""
from __future__ import annotations
from datetime import datetime, time, timezone
from zoneinfo import ZoneInfo

WIB = ZoneInfo("Asia/Jakarta")


def _parse_hhmm(value, fallback):
    try:
        hh, mm = str(value).strip().split(":", 1)
        return time(int(hh), int(mm))
    except Exception:
        hh, mm = fallback.split(":", 1)
        return time(int(hh), int(mm))


def session_status(config=None, now=None):
    cfg = config or {}
    local = now or datetime.now(timezone.utc).astimezone(WIB)
    if local.tzinfo is None:
        local = local.replace(tzinfo=WIB)
    else:
        local = local.astimezone(WIB)

    day = local.date().isoformat()
    weekday = local.weekday()
    holidays = {str(x) for x in (cfg.get("market_holidays") or [])}

    if weekday >= 5:
        return {"live": False, "status": "WEEKEND", "wib": local.isoformat(), "market_date": day}
    if day in holidays:
        return {"live": False, "status": "HOLIDAY", "wib": local.isoformat(), "market_date": day}

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
            return {"live": True, "status": name, "wib": local.isoformat(), "market_date": day}

    if current < sessions[0][0]:
        status = "PRE_MARKET"
    elif sessions[0][1] <= current < sessions[1][0]:
        status = "BREAK"
    else:
        status = "CLOSED"
    return {"live": False, "status": status, "wib": local.isoformat(), "market_date": day}


def is_live_market_session(config=None, now=None):
    return bool(session_status(config=config, now=now).get("live"))
