"""Deadline-first H-10 Pre-Market scheduler.

This module exists because the ordinary 30-minute engine loop cannot guarantee
that a recommendation snapshot is finalized before the IDX 09:00 WIB open.
It runs as a small independent daemon inside the detached worker process.

Contract:
- 08:30-08:44 WIB: build full-universe Pre-Market drafts from stored market data.
- 08:45-08:49 WIB: refresh the draft from the newest production snapshot only,
  avoiding a long scan that could cross the deadline.
- 08:50 WIB: atomically replace any earlier same-day provisional run with the
  final H-10 snapshot.
- After 09:00 WIB: never rewrite the locked snapshot. No hindsight edits.
- Strict BUY picks stay BUY. If none clears every strict gate, up to five best
  non-SELL candidates are published as honest WAIT/WATCH/MONITOR candidates,
  never upgraded to BUY merely to fill the screen.
"""
from __future__ import annotations

import json
import sqlite3
import time
import traceback
from datetime import datetime, time as dt_time, timezone
from zoneinfo import ZoneInfo

import pandas as pd

import app
import market_session
import premarket_intelligence

WIB = ZoneInfo("Asia/Jakarta")
CFG = app.CFGD
VERSION = "H10-R1"

PREPARE_START = str(CFG.get("premarket_prepare_start", "08:30"))
LIGHTWEIGHT_START = str(CFG.get("premarket_lightweight_start", "08:45"))
FINAL_LOCK = str(CFG.get("premarket_final_lock", "08:50"))
MARKET_OPEN = str(CFG.get("premarket_market_open", "09:00"))
HEAVY_REFRESH_MINUTES = max(1, int(CFG.get("premarket_heavy_refresh_minutes", 5)))
LIGHT_REFRESH_SECONDS = max(15, int(CFG.get("premarket_light_refresh_seconds", 60)))
MAX_PICKS = max(1, min(5, int(CFG.get("premarket_max_picks", 5))))

_PICK_FIELDS = [
    "ticker", "company", "decision", "confidence", "jst_confidence",
    "jst_p_up", "ai_score", "risk", "close", "entry_low", "entry_high",
    "stop_loss", "target_1", "target_2", "market_regime",
    "decision_priority", "decision_score", "relative_rank_score",
]


def _parse_hhmm(value: str, fallback: str) -> dt_time:
    try:
        hh, mm = str(value).strip().split(":", 1)
        return dt_time(int(hh), int(mm))
    except Exception:
        hh, mm = fallback.split(":", 1)
        return dt_time(int(hh), int(mm))


def _now_wib(now=None) -> datetime:
    if now is None:
        return datetime.now(timezone.utc).astimezone(WIB)
    if now.tzinfo is None:
        return now.replace(tzinfo=WIB)
    return now.astimezone(WIB)


def _is_trading_day(local: datetime) -> bool:
    return market_session.is_trading_date(local.date(), CFG)


def window_state(now=None) -> str:
    """Return BEFORE, PREPARE, LIGHT, FINALIZE, AFTER_OPEN, or OFF."""
    local = _now_wib(now)
    if not _is_trading_day(local):
        return "OFF"
    current = local.time().replace(tzinfo=None)
    prepare = _parse_hhmm(PREPARE_START, "08:30")
    light = _parse_hhmm(LIGHTWEIGHT_START, "08:45")
    final = _parse_hhmm(FINAL_LOCK, "08:50")
    market_open = _parse_hhmm(MARKET_OPEN, "09:00")
    if current < prepare:
        return "BEFORE"
    if current < light:
        return "PREPARE"
    if current < final:
        return "LIGHT"
    if current < market_open:
        return "FINALIZE"
    return "AFTER_OPEN"


def _target_market_date(now=None) -> str:
    return market_session.next_trading_date(
        CFG,
        now=_now_wib(now),
        after_close=True,
    )


def _state_get(key, default=""):
    try:
        value = app.state_get(key)
        return default if value is None else value
    except Exception:
        return default


def _state_set(key, value):
    try:
        app.state_set(key, value)
    except Exception:
        pass


def _log(status, detail=""):
    stamp = datetime.now(timezone.utc).isoformat()
    text = f"[{stamp}] PREMARKET_H10 {status} {detail}"
    print(text, flush=True)
    try:
        con = sqlite3.connect(app.DB, timeout=30)
        con.execute("PRAGMA busy_timeout=30000")
        con.execute(
            "INSERT INTO engine_log(ts,phase,status,detail) VALUES(?,?,?,?)",
            (stamp, "PREMARKET_H10", str(status), str(detail)[:2000]),
        )
        con.commit()
        con.close()
    except Exception:
        pass


def _ensure_tables():
    con = sqlite3.connect(app.DB, timeout=30)
    con.execute("PRAGMA busy_timeout=30000")
    con.execute("""CREATE TABLE IF NOT EXISTS premarket_picks (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        market_date TEXT NOT NULL, generated_at TEXT NOT NULL,
        rank INTEGER, ticker TEXT, company TEXT, decision TEXT,
        confidence REAL, ai_score REAL, risk TEXT,
        reference_price REAL,
        entry_low REAL, entry_high REAL, stop_loss REAL,
        target_1 REAL, target_2 REAL, ihsg_regime TEXT,
        reason TEXT, source_phase TEXT,
        UNIQUE(market_date, rank))""")
    con.execute("""CREATE TABLE IF NOT EXISTS premarket_runs (
        market_date TEXT PRIMARY KEY, generated_at TEXT NOT NULL,
        status TEXT NOT NULL, pick_count INTEGER NOT NULL, note TEXT)""")
    existing = {str(r[1]) for r in con.execute("PRAGMA table_info(premarket_picks)").fetchall()}
    if "reference_price" not in existing:
        con.execute("ALTER TABLE premarket_picks ADD COLUMN reference_price REAL")
    con.commit()
    con.close()


def _number(value, default=None):
    try:
        if value is None or pd.isna(value):
            return default
        return float(value)
    except Exception:
        return default


def _confidence(row):
    value = _number(row.get("confidence"))
    if value is None:
        value = _number(row.get("jst_confidence"))
    if value is None:
        value = _number(row.get("jst_p_up"))
    if value is not None and value <= 1:
        value *= 100.0
    return value


def _read_latest_snapshot(limit=1200) -> pd.DataFrame:
    try:
        con = sqlite3.connect(app.DB, timeout=30)
        con.execute("PRAGMA busy_timeout=30000")
        d = pd.read_sql_query(
            "SELECT * FROM opportunities ORDER BY rank LIMIT ?",
            con,
            params=(int(limit),),
        )
        con.close()
        return d
    except Exception:
        return pd.DataFrame()


def _load_model():
    for name in ("load_active_model", "load_jst"):
        try:
            loader = getattr(app, name, None)
            if callable(loader):
                model = loader()
                if model is not None:
                    return model
        except Exception:
            pass
    return None


def _fresh_full_analysis() -> pd.DataFrame:
    """Analyze the full stored universe without waiting for the ordinary cycle."""
    try:
        market = app.fetch_ihsg() if hasattr(app, "fetch_ihsg") else None
        if market is not None and hasattr(market, "empty") and not market.empty:
            try:
                app.save_ihsg(market)
            except Exception:
                pass
    except Exception:
        pass

    universe, _ = app.load_universe()
    if universe is None or universe.empty:
        return _read_latest_snapshot()

    model = _load_model()
    try:
        result = app.analyze_all(universe, model)
    except TypeError:
        result = app.analyze_all(universe)
    except Exception:
        result = pd.DataFrame()

    if result is None or result.empty:
        return _read_latest_snapshot()
    return result


def _select_monitor_candidates(res: pd.DataFrame) -> pd.DataFrame:
    """Return best available non-SELL rows without inventing a BUY signal."""
    if res is None or res.empty:
        return pd.DataFrame()
    c = res.copy()
    if "decision" not in c.columns:
        c["decision"] = "MONITOR"
    source_decision = c["decision"].astype(str).str.upper().str.strip()
    sell = source_decision.isin({"SELL", "SELL/AVOID", "AVOID", "STRONG_SELL"})
    c = c[~sell].copy()
    source_decision = source_decision.loc[c.index]
    if c.empty:
        return c

    decision_rank = {
        "BUY": 0, "STRONG_BUY": 0, "NEAR ENTRY": 1, "WATCH": 2,
        "WAIT": 3, "HOLD": 3, "ANALYSIS": 4, "MONITOR": 4,
    }
    c["_decision_rank"] = source_decision.map(decision_rank).fillna(5)
    risk_rank = {"LOW": 0, "MEDIUM": 1, "HIGH": 2, "EXTREME": 3}
    if "risk" in c.columns:
        c["_risk_rank"] = c["risk"].astype(str).str.upper().map(risk_rank).fillna(2)
    else:
        c["_risk_rank"] = 2

    sort_cols = ["_decision_rank", "_risk_rank"]
    ascending = [True, True]
    for col in ("decision_score", "relative_rank_score", "ai_score", "jst_p_up"):
        if col in c.columns:
            c[col] = pd.to_numeric(c[col], errors="coerce")
            sort_cols.append(col)
            ascending.append(False)
    c = c.sort_values(sort_cols, ascending=ascending, kind="stable").head(MAX_PICKS).copy()

    # A BUY row that failed the strict Pre-Market gates is explicitly demoted.
    # This is the key anti-fabrication rule: filling the card must never create BUY.
    original = c["decision"].astype(str).str.upper().str.strip()
    c.loc[original.isin({"BUY", "STRONG_BUY"}), "decision"] = "WAIT"
    c.drop(columns=["_decision_rank", "_risk_rank"], errors="ignore", inplace=True)
    return c.reset_index(drop=True)


def select_candidates(res: pd.DataFrame):
    """Strict BUY first; otherwise honest best-available monitor candidates."""
    strict = premarket_intelligence.select_high_conviction(res, CFG)
    picks = strict.get("picks")
    if picks is not None and not picks.empty:
        return picks.head(MAX_PICKS).reset_index(drop=True), "BUY_READY", str(strict.get("reason") or "")
    monitor = _select_monitor_candidates(res)
    if not monitor.empty:
        return (
            monitor,
            "MONITOR_FALLBACK",
            str(strict.get("reason") or "No strict BUY passed; showing best available monitor candidates."),
        )
    return pd.DataFrame(), "NO_TRADE", str(strict.get("reason") or "No usable Pre-Market candidate.")


def _record_from_row(row, market_date, generated_at, rank, mode, reason):
    close = _number(row.get("close"))
    lo = _number(row.get("entry_low"))
    hi = _number(row.get("entry_high"))
    if lo is None and close is not None:
        lo = close * 0.985
    if hi is None and close is not None:
        hi = close * 1.015
    decision = str(row.get("decision") or "MONITOR").upper().strip()
    if mode == "MONITOR_FALLBACK" and decision in {"BUY", "STRONG_BUY"}:
        decision = "WAIT"
    return {
        "market_date": market_date,
        "generated_at": generated_at,
        "rank": int(rank),
        "ticker": str(row.get("ticker") or "").upper().replace(".JK", "").strip(),
        "company": str(row.get("company") or ""),
        "decision": decision,
        "confidence": _confidence(row),
        "ai_score": _number(row.get("ai_score")),
        "risk": str(row.get("risk") or ""),
        "reference_price": close,
        "entry_low": lo,
        "entry_high": hi,
        "stop_loss": _number(row.get("stop_loss")),
        "target_1": _number(row.get("target_1")),
        "target_2": _number(row.get("target_2")),
        "ihsg_regime": str(row.get("market_regime") or ""),
        "reason": (
            "Strict H-10 BUY candidate." if mode == "BUY_READY"
            else "MONITOR ONLY: strict BUY gate not passed. " + str(reason or "")
        )[:900],
        "source_phase": f"premarket-h10:{VERSION}:{mode}",
    }


def _save_draft(res: pd.DataFrame, market_date: str, mode: str, reason: str):
    picks, selected_mode, selected_reason = select_candidates(res)
    if selected_mode:
        mode = selected_mode
    if selected_reason:
        reason = selected_reason
    generated_at = datetime.now(timezone.utc).isoformat()
    rows = [
        _record_from_row(row, market_date, generated_at, i + 1, mode, reason)
        for i, (_, row) in enumerate(picks.iterrows())
    ]
    payload = {
        "version": VERSION,
        "market_date": market_date,
        "generated_at": generated_at,
        "mode": mode,
        "reason": reason,
        "picks": rows,
    }
    _state_set("premarket_h10_draft_json", json.dumps(payload, ensure_ascii=False, default=str))
    _state_set("premarket_h10_draft_at", generated_at)
    _state_set("premarket_h10_draft_market_date", market_date)
    _state_set("premarket_h10_draft_mode", mode)
    _state_set("premarket_h10_draft_count", len(rows))
    _log("DRAFT", f"market_date={market_date} mode={mode} picks={len(rows)}")
    return payload


def build_draft(now=None, lightweight=False):
    market_date = _target_market_date(now)
    if lightweight:
        res = _read_latest_snapshot()
        if res.empty:
            res = _fresh_full_analysis()
        source = "snapshot-refresh"
    else:
        res = _fresh_full_analysis()
        source = "full-stored-universe-analysis"
    picks, mode, reason = select_candidates(res)
    # _save_draft deliberately runs selection again on the original frame so the
    # exact anti-fabrication rules remain centralized in one place.
    payload = _save_draft(res, market_date, mode, reason)
    payload["source"] = source
    return payload


def _load_draft(market_date: str):
    try:
        payload = json.loads(str(_state_get("premarket_h10_draft_json", "")) or "{}")
        if str(payload.get("market_date")) != str(market_date):
            return None
        return payload
    except Exception:
        return None


def _already_finalized(market_date: str) -> bool:
    try:
        con = sqlite3.connect(app.DB, timeout=30)
        row = con.execute(
            "SELECT note FROM premarket_runs WHERE market_date=?",
            (market_date,),
        ).fetchone()
        con.close()
        return bool(row and str(row[0] or "").startswith("H10_FINAL"))
    except Exception:
        return False


def finalize_h10(now=None):
    """Atomically replace provisional same-day output with the final H-10 list."""
    local = _now_wib(now)
    market_date = _target_market_date(local)
    if _already_finalized(market_date):
        return {"status": "ALREADY_FINAL", "market_date": market_date}

    payload = _load_draft(market_date)
    if not payload:
        # Deadline path must be fast. Prefer the newest production snapshot;
        # only run a full stored-universe analysis when no snapshot exists.
        res = _read_latest_snapshot()
        if res.empty:
            payload = build_draft(local, lightweight=False)
        else:
            picks, mode, reason = select_candidates(res)
            payload = _save_draft(res, market_date, mode, reason)

    picks = list(payload.get("picks") or [])
    mode = str(payload.get("mode") or "NO_TRADE")
    generated_at = datetime.now(timezone.utc).isoformat()
    status = "LOCKED" if picks else "NO_TRADE"
    note = (
        f"H10_FINAL {VERSION} mode={mode} final_wib={local.isoformat()} "
        f"draft_at={payload.get('generated_at','')} reason={payload.get('reason','')}"
    )[:1800]

    _ensure_tables()
    con = sqlite3.connect(app.DB, timeout=30)
    con.execute("PRAGMA busy_timeout=30000")
    try:
        con.execute("BEGIN IMMEDIATE")
        # Any earlier run for this date is provisional by definition until H-10.
        con.execute("DELETE FROM premarket_picks WHERE market_date=?", (market_date,))
        con.execute("DELETE FROM premarket_runs WHERE market_date=?", (market_date,))
        for i, item in enumerate(picks, start=1):
            con.execute(
                """INSERT INTO premarket_picks
                (market_date,generated_at,rank,ticker,company,decision,confidence,
                 ai_score,risk,reference_price,entry_low,entry_high,stop_loss,target_1,target_2,
                 ihsg_regime,reason,source_phase)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    market_date, generated_at, i, str(item.get("ticker") or ""),
                    str(item.get("company") or ""), str(item.get("decision") or "MONITOR"),
                    _number(item.get("confidence")), _number(item.get("ai_score")),
                    str(item.get("risk") or ""), _number(item.get("reference_price")),
                    _number(item.get("entry_low")),
                    _number(item.get("entry_high")), _number(item.get("stop_loss")),
                    _number(item.get("target_1")), _number(item.get("target_2")),
                    str(item.get("ihsg_regime") or ""), str(item.get("reason") or ""),
                    str(item.get("source_phase") or f"premarket-h10:{VERSION}:{mode}"),
                ),
            )
        con.execute(
            """INSERT INTO premarket_runs
            (market_date,generated_at,status,pick_count,note)
            VALUES(?,?,?,?,?)""",
            (market_date, generated_at, status, len(picks), note),
        )
        con.commit()
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()

    final_time = local
    deadline = _parse_hhmm(FINAL_LOCK, "08:50")
    deadline_seconds = deadline.hour * 3600 + deadline.minute * 60
    actual_seconds = final_time.hour * 3600 + final_time.minute * 60 + final_time.second
    timing = "ON_TIME" if actual_seconds <= deadline_seconds + 15 else "LATE"

    _state_set("premarket_status", status)
    _state_set("premarket_market_date", market_date)
    _state_set("premarket_pick_count", len(picks))
    _state_set("premarket_locked_at", generated_at)
    _state_set("premarket_h10_finalized_at", generated_at)
    _state_set("premarket_h10_market_date", market_date)
    _state_set("premarket_h10_mode", mode)
    _state_set("premarket_h10_timing", timing)
    _state_set("premarket_h10_version", VERSION)
    _log("FINAL", f"market_date={market_date} status={status} mode={mode} picks={len(picks)} timing={timing}")
    return {
        "status": status,
        "market_date": market_date,
        "mode": mode,
        "pick_count": len(picks),
        "timing": timing,
    }


def _draft_due(market_date: str, seconds: int) -> bool:
    if str(_state_get("premarket_h10_draft_market_date", "")) != str(market_date):
        return True
    raw = str(_state_get("premarket_h10_draft_at", "") or "")
    if not raw:
        return True
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return (datetime.now(timezone.utc) - dt.astimezone(timezone.utc)).total_seconds() >= seconds
    except Exception:
        return True


def run_forever():
    _log(
        "START",
        f"version={VERSION} prepare={PREPARE_START} light={LIGHTWEIGHT_START} final={FINAL_LOCK} open={MARKET_OPEN}",
    )
    while True:
        try:
            local = _now_wib()
            state = window_state(local)
            market_date = _target_market_date(local)
            if state == "PREPARE":
                if _draft_due(market_date, HEAVY_REFRESH_MINUTES * 60):
                    build_draft(local, lightweight=False)
                time.sleep(15)
                continue
            if state == "LIGHT":
                if _draft_due(market_date, LIGHT_REFRESH_SECONDS):
                    build_draft(local, lightweight=True)
                # Tight polling here exists solely to honor the 08:50 deadline.
                time.sleep(5)
                continue
            if state == "FINALIZE":
                if not _already_finalized(market_date):
                    finalize_h10(local)
                time.sleep(5)
                continue
            # Outside the deadline window this daemon intentionally stays quiet.
            time.sleep(60)
        except Exception:
            _log("ERROR", traceback.format_exc()[-1800:])
            time.sleep(15)


if __name__ == "__main__":
    run_forever()
