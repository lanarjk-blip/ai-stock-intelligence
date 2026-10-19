"""R47 live precision and green-strength guardrails.

This patch deliberately strengthens live decision quality without changing the
trained JST model. It compares historical/model evidence with the current
intraday tape, broad-market breadth, and relative strength before allowing an
ENTRY READY state.

It is installed from accuracy_guardrails after dynamic_engine is imported.
"""
from __future__ import annotations

import math
import os
import re
import sqlite3
import threading
import time
from datetime import datetime, timezone

import numpy as np
import pandas as pd

_INSTALLED = False
_WORKER_PATCHED = False
_AUDIT_THREAD_STARTED = False
_BREADTH_CACHE = {"at": 0.0, "market_date": "", "value": None}

_ORIGINAL_SCORE = None
_ORIGINAL_STATE = None
_ORIGINAL_TIME_SIGNAL = None
_ORIGINAL_UPDATE = None
_ORIGINAL_INTRADAY_CANDIDATES = None


def _num(value, default=None):
    try:
        value = float(value)
        return value if math.isfinite(value) else default
    except Exception:
        return default


def _ticker(value):
    return str(value or "").upper().replace(".JK", "").strip()


def _dbcon():
    import app
    return app.dbcon()


def _session(now=None):
    import app
    import market_session
    return market_session.session_status(app.CFGD, now=now)


def live_metrics(ticker, now=None):
    """Return same-session live evidence, fail-closed when bars are unavailable."""
    ticker = _ticker(ticker)
    session = _session(now)
    live_required = bool(session.get("live"))
    market_date = str(session.get("market_date") or "")
    out = {
        "ticker": ticker,
        "live_required": live_required,
        "fresh": False,
        "bars": 0,
        "market_date": market_date,
        "last_ts": "",
        "age_minutes": None,
        "open_price": None,
        "current_price": None,
        "return_from_open_pct": None,
        "momentum_pct": None,
        "volume_ratio": None,
        "range_position": None,
    }
    if not live_required:
        return out
    try:
        con = _dbcon()
        df = pd.read_sql_query(
            "SELECT ts,open,high,low,close,volume FROM intraday WHERE ticker=? ORDER BY ts DESC LIMIT 40",
            con,
            params=(ticker,),
        )
        con.close()
        if df.empty:
            return out
        df["ts"] = pd.to_datetime(df["ts"], utc=True, errors="coerce")
        for col in ("open", "high", "low", "close", "volume"):
            df[col] = pd.to_numeric(df[col], errors="coerce")
        df = df.dropna(subset=["ts", "close"])
        if df.empty:
            return out
        df["wib"] = df["ts"].dt.tz_convert("Asia/Jakarta")
        df = df[df["wib"].dt.date.astype(str) == market_date].sort_values("ts")
        if df.empty:
            return out
        last = df.iloc[-1]
        first = df.iloc[0]
        local_now = pd.Timestamp(session.get("wib"))
        if local_now.tzinfo is None:
            local_now = local_now.tz_localize("Asia/Jakarta")
        age_minutes = max(
            0.0,
            (local_now.tz_convert("UTC") - last["ts"]).total_seconds() / 60.0,
        )
        import app
        stale_limit = max(10, int(app.CFGD.get("intraday_live_stale_minutes", 30)))
        fresh = age_minutes <= stale_limit
        open_price = _num(first.get("open"), _num(first.get("close")))
        current = _num(last.get("close"))
        ret = ((current / open_price) - 1.0) * 100.0 if current and open_price else None
        if len(df) >= 2:
            prev = _num(df.iloc[-2].get("close"))
            momentum = ((current / prev) - 1.0) * 100.0 if current and prev else None
        else:
            momentum = 0.0
        vols = pd.to_numeric(df["volume"], errors="coerce").dropna()
        if len(vols) >= 3 and _num(vols.iloc[-1]) is not None:
            baseline = _num(vols.iloc[:-1].tail(6).median(), 0.0)
            volume_ratio = float(vols.iloc[-1]) / baseline if baseline and baseline > 0 else None
        else:
            volume_ratio = None
        high = _num(df["high"].max())
        low = _num(df["low"].min())
        range_position = ((current - low) / (high - low)) if current is not None and high is not None and low is not None and high > low else 0.5
        out.update({
            "fresh": bool(fresh),
            "bars": int(len(df)),
            "last_ts": last["ts"].isoformat(),
            "age_minutes": age_minutes,
            "open_price": open_price,
            "current_price": current,
            "return_from_open_pct": ret,
            "momentum_pct": momentum,
            "volume_ratio": volume_ratio,
            "range_position": range_position,
        })
        return out
    except Exception:
        try:
            con.close()
        except Exception:
            pass
        return out


def market_breadth(now=None, force=False):
    """Estimate stored live breadth from same-session intraday bars."""
    session = _session(now)
    live = bool(session.get("live"))
    market_date = str(session.get("market_date") or "")
    empty = {
        "live": live,
        "market_date": market_date,
        "symbols": 0,
        "green": 0,
        "red": 0,
        "flat": 0,
        "green_ratio": None,
        "red_ratio": None,
        "defensive": False,
    }
    if not live:
        return empty
    now_mono = time.monotonic()
    if (
        not force
        and _BREADTH_CACHE.get("value") is not None
        and _BREADTH_CACHE.get("market_date") == market_date
        and now_mono - float(_BREADTH_CACHE.get("at") or 0) < 90
    ):
        return dict(_BREADTH_CACHE["value"])
    try:
        con = _dbcon()
        df = pd.read_sql_query(
            "SELECT ticker,ts,open,close FROM intraday ORDER BY ticker,ts",
            con,
        )
        con.close()
        if df.empty:
            return empty
        df["ts"] = pd.to_datetime(df["ts"], utc=True, errors="coerce")
        df["open"] = pd.to_numeric(df["open"], errors="coerce")
        df["close"] = pd.to_numeric(df["close"], errors="coerce")
        df = df.dropna(subset=["ticker", "ts", "close"])
        df["wib"] = df["ts"].dt.tz_convert("Asia/Jakarta")
        df = df[df["wib"].dt.date.astype(str) == market_date]
        if df.empty:
            return empty
        grouped = []
        for ticker, g in df.groupby("ticker", sort=False):
            g = g.sort_values("ts")
            op = _num(g.iloc[0].get("open"), _num(g.iloc[0].get("close")))
            cur = _num(g.iloc[-1].get("close"))
            if not op or cur is None:
                continue
            grouped.append(((cur / op) - 1.0) * 100.0)
        if not grouped:
            return empty
        arr = np.asarray(grouped, dtype=float)
        green = int(np.sum(arr > 0.15))
        red = int(np.sum(arr < -0.15))
        flat = int(len(arr) - green - red)
        symbols = int(len(arr))
        import app
        min_symbols = int(app.CFGD.get("defensive_breadth_min_symbols", 20))
        red_cut = float(app.CFGD.get("defensive_breadth_red_ratio", 0.65))
        result = {
            "live": True,
            "market_date": market_date,
            "symbols": symbols,
            "green": green,
            "red": red,
            "flat": flat,
            "green_ratio": green / symbols,
            "red_ratio": red / symbols,
            "defensive": symbols >= min_symbols and (red / symbols) >= red_cut,
        }
        _BREADTH_CACHE.update({"at": now_mono, "market_date": market_date, "value": dict(result)})
        return result
    except Exception:
        return empty


def _ihsg_change_pct():
    try:
        con = _dbcon()
        row = con.execute(
            "SELECT change_pct FROM market_index WHERE symbol='IHSG' ORDER BY date DESC LIMIT 1"
        ).fetchone()
        con.close()
        return _num(row[0]) if row else None
    except Exception:
        return None


def relative_strength_pct(row, metrics=None):
    """Prefer model rel_ret1; otherwise compare live stock return to latest IHSG move."""
    for key in ("rel_ret1", "relative_return", "relative_strength"):
        try:
            value = _num(row.get(key) if hasattr(row, "get") else getattr(row, key, None))
            if value is not None:
                return value * 100.0 if abs(value) <= 1.5 else value
        except Exception:
            pass
    metrics = metrics or live_metrics(getattr(row, "ticker", None))
    stock_ret = _num(metrics.get("return_from_open_pct"))
    ihsg = _ihsg_change_pct()
    if stock_ret is None:
        return None
    return stock_ret - (ihsg or 0.0)


def map_live_match(up_pct, down_pct, metrics):
    """Compare Time Map directional bias with the current tape.

    This is a transparent heuristic agreement score, not a calibrated probability.
    """
    up = _num(up_pct)
    down = _num(down_pct)
    if up is None or down is None or not metrics or not metrics.get("fresh"):
        return "DATA INTRADAY BELUM CUKUP", None
    ret = _num(metrics.get("return_from_open_pct"), 0.0)
    mom = _num(metrics.get("momentum_pct"), 0.0)
    rs = _num(metrics.get("relative_strength_pct"), 0.0)
    model_bias = up - down
    live_bias = np.clip(ret * 16.0 + mom * 28.0 + rs * 10.0, -100.0, 100.0)
    same_direction = (model_bias >= 5 and live_bias > 5) or (model_bias <= -5 and live_bias < -5)
    opposite = (model_bias >= 10 and live_bias < -8) or (model_bias <= -10 and live_bias > 8)
    magnitude = min(100.0, abs(model_bias) * 0.55 + abs(live_bias) * 0.45)
    if opposite:
        return "DIVERGENCE", max(0.0, 25.0 - magnitude * 0.15)
    if same_direction and magnitude >= 35:
        return "MATCH STRONG", min(100.0, 70.0 + magnitude * 0.3)
    if same_direction:
        return "MATCH", min(85.0, 55.0 + magnitude * 0.3)
    return "MIXED", min(60.0, 40.0 + magnitude * 0.15)


def score_adjustment(row, metrics, breadth=None):
    """Live confirmation adjustment with asymmetric penalties for red momentum."""
    if not metrics or not metrics.get("live_required"):
        return 0.0
    if not metrics.get("fresh"):
        return -10.0
    ret = _num(metrics.get("return_from_open_pct"), 0.0)
    mom = _num(metrics.get("momentum_pct"), 0.0)
    vol = _num(metrics.get("volume_ratio"), 1.0)
    pos = _num(metrics.get("range_position"), 0.5)
    rs = _num(metrics.get("relative_strength_pct"), 0.0)
    adj = 0.0
    adj += float(np.clip(ret * 1.6, -12, 10))
    adj += float(np.clip(mom * 3.0, -9, 7))
    adj += float(np.clip(rs * 1.5, -9, 9))
    adj += float(np.clip((pos - 0.5) * 8.0, -4, 4))
    if vol is not None:
        if vol >= 1.35 and ret > 0:
            adj += 3.0
        elif vol >= 1.35 and ret < 0:
            adj -= 4.0
    breadth = breadth or market_breadth()
    if breadth.get("defensive"):
        if ret > 0.5 and rs > 0.5 and mom >= 0:
            adj += 4.0
        elif ret < 0 or rs < 0:
            adj -= 7.0
    return float(np.clip(adj, -22.0, 16.0))


def guard_state(row, ts, score, base_state=None, metrics=None, breadth=None):
    """Fail-closed live gate layered on top of the existing dynamic state."""
    base_state = str(base_state or "MONITOR").upper()
    metrics = metrics or live_metrics(getattr(row, "ticker", None))
    if not metrics.get("live_required"):
        return base_state
    risk = str(getattr(row, "risk", "") or "").upper()
    if risk in {"HIGH", "EXTREME"}:
        return "MONITOR"
    if not metrics.get("fresh"):
        return "MONITOR"
    ret = _num(metrics.get("return_from_open_pct"), 0.0)
    mom = _num(metrics.get("momentum_pct"), 0.0)
    rs = _num(metrics.get("relative_strength_pct"), 0.0)
    match = str(ts.get("live_match") or "")
    if ret <= -1.0 or mom <= -0.8:
        return "MONITOR"
    if match == "DIVERGENCE":
        return "DEVELOPING" if base_state in {"ENTRY READY", "DEVELOPING"} else "MONITOR"
    breadth = breadth or market_breadth()
    if breadth.get("defensive"):
        if score < 72 or ret < 0.4 or rs < 0.5 or mom < -0.1:
            return "DEVELOPING" if base_state == "ENTRY READY" else base_state
    if base_state == "ENTRY READY" and (ret < -0.15 or rs < -0.25):
        return "DEVELOPING"
    return base_state


def _patched_time_signal(ticker):
    base = _ORIGINAL_TIME_SIGNAL(ticker)
    metrics = live_metrics(ticker)
    metrics["relative_strength_pct"] = relative_strength_pct({"ticker": ticker}, metrics)
    edge = _num(base.get("edge"), 0.0) * 100.0
    up = float(np.clip(50.0 + edge, 0.0, 100.0))
    down = float(np.clip(50.0 - edge, 0.0, 100.0))
    match, match_score = map_live_match(up, down, metrics)
    out = dict(base)
    out.update({
        "live_match": match,
        "live_match_score": match_score,
        "live_return_pct": metrics.get("return_from_open_pct"),
        "live_momentum_pct": metrics.get("momentum_pct"),
        "live_volume_ratio": metrics.get("volume_ratio"),
        "live_range_position": metrics.get("range_position"),
        "relative_strength_pct": metrics.get("relative_strength_pct"),
    })
    return out


def _patched_score(row, ts):
    base = float(_ORIGINAL_SCORE(row, ts))
    metrics = live_metrics(getattr(row, "ticker", None))
    metrics["relative_strength_pct"] = relative_strength_pct(row, metrics)
    return float(np.clip(base + score_adjustment(row, metrics), 0, 100))


def _patched_state(row, ts, score):
    base = _ORIGINAL_STATE(row, ts, score)
    metrics = live_metrics(getattr(row, "ticker", None))
    metrics["relative_strength_pct"] = relative_strength_pct(row, metrics)
    return guard_state(row, ts, score, base_state=base, metrics=metrics)


def _append_live_reason():
    try:
        con = _dbcon()
        rows = con.execute(
            "SELECT ticker,reason FROM dynamic_opportunities"
        ).fetchall()
        for ticker, reason in rows:
            m = live_metrics(ticker)
            m["relative_strength_pct"] = relative_strength_pct({"ticker": ticker}, m)
            if not m.get("live_required"):
                continue
            live_text = (
                f"LIVE ret={_num(m.get('return_from_open_pct'),0):+.2f}% "
                f"mom={_num(m.get('momentum_pct'),0):+.2f}% "
                f"RS={_num(m.get('relative_strength_pct'),0):+.2f}% "
                f"vol={_num(m.get('volume_ratio'),0):.2f}x fresh={bool(m.get('fresh'))}"
            )
            current = str(reason or "")
            if "LIVE ret=" in current:
                current = re.sub(r"LIVE ret=.*$", live_text, current)
            else:
                current = (current + "; " + live_text).strip("; ")
            con.execute(
                "UPDATE dynamic_opportunities SET reason=? WHERE ticker=?",
                (current[:1900], _ticker(ticker)),
            )
        con.commit()
        con.close()
    except Exception:
        try:
            con.close()
        except Exception:
            pass


def _patched_update(res, phase="CLOSED"):
    out = _ORIGINAL_UPDATE(res, phase=phase)
    try:
        breadth = market_breadth(force=True)
        import app
        app.state_set("market_breadth_symbols", breadth.get("symbols", 0))
        app.state_set("market_breadth_green_ratio", breadth.get("green_ratio", ""))
        app.state_set("market_breadth_red_ratio", breadth.get("red_ratio", ""))
        app.state_set("market_defensive_mode", int(bool(breadth.get("defensive"))))
        _append_live_reason()
    except Exception:
        pass
    return out


def record_search_priority(ticker):
    """Persist the user's direct ticker interest for the next intraday cycle."""
    ticker = _ticker(ticker)
    if not ticker or not re.fullmatch(r"[A-Z0-9]{2,6}", ticker):
        return False
    try:
        import app
        app.state_set("priority_search_ticker", ticker)
        app.state_set("priority_search_at", datetime.now(timezone.utc).isoformat())
        return True
    except Exception:
        return False


def _priority_search_ticker():
    try:
        import app
        ticker = _ticker(app.state_get("priority_search_ticker"))
        raw = str(app.state_get("priority_search_at") or "")
        if not ticker or not raw:
            return ""
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        ttl = int(app.CFGD.get("priority_search_ttl_minutes", 120))
        age = (datetime.now(timezone.utc) - dt.astimezone(timezone.utc)).total_seconds() / 60.0
        return ticker if age <= ttl else ""
    except Exception:
        return ""


def priority_sort_frame(res):
    if res is None or res.empty:
        return pd.DataFrame()
    c = res.copy()
    for col in ("rel_ret1", "ret1", "vol_ratio", "relative_rank_score", "ai_score", "jst_p_up"):
        if col not in c.columns:
            c[col] = 0.0
        c[col] = pd.to_numeric(c[col], errors="coerce").fillna(0.0)
    c["_green_strength"] = (
        0.30 * c["rel_ret1"].clip(-0.20, 0.20) * 100
        + 0.22 * c["ret1"].clip(-0.20, 0.20) * 100
        + 0.12 * c["vol_ratio"].clip(0, 4) * 10
        + 0.18 * c["relative_rank_score"].clip(0, 100)
        + 0.10 * c["ai_score"].clip(0, 100)
        + 0.08 * c["jst_p_up"].clip(0, 1) * 100
    )
    return c.sort_values("_green_strength", ascending=False, kind="stable")


def _patch_worker_priority():
    global _WORKER_PATCHED, _ORIGINAL_INTRADAY_CANDIDATES
    if _WORKER_PATCHED:
        return
    try:
        import worker_base
    except Exception:
        return
    if getattr(worker_base._intraday_candidate_tickers, "_r47_priority", False):
        _WORKER_PATCHED = True
        return
    _ORIGINAL_INTRADAY_CANDIDATES = worker_base._intraday_candidate_tickers

    def candidate_tickers(res, now=None):
        import app
        base = list(_ORIGINAL_INTRADAY_CANDIDATES(res, now=now) or [])
        ordered = []
        def add(v):
            t = _ticker(v)
            if t and t not in ordered:
                ordered.append(t)
        add(_priority_search_ticker())
        for t in base:
            add(t)
        if res is not None and not res.empty:
            ranked = priority_sort_frame(res)
            for t in ranked.get("ticker", pd.Series(dtype=str)).tolist():
                add(t)
        regime = ""
        try:
            if res is not None and not res.empty and "market_regime" in res.columns:
                mode = res["market_regime"].astype(str).str.upper().mode()
                regime = str(mode.iloc[0]) if not mode.empty else ""
        except Exception:
            pass
        base_limit = max(60, int(app.CFGD.get("intraday_symbols_per_cycle", 80)))
        limit = min(120, max(base_limit, 100 if regime in {"HIGH_VOLATILITY", "BEARISH"} else base_limit))
        return ordered[:limit]

    candidate_tickers._r47_priority = True
    worker_base._intraday_candidate_tickers = candidate_tickers
    _WORKER_PATCHED = True


def _ensure_audit_table():
    con = _dbcon()
    con.execute("""
        CREATE TABLE IF NOT EXISTS precision_daily_audit (
            market_date TEXT NOT NULL,
            ticker TEXT NOT NULL,
            audit_type TEXT NOT NULL,
            actual_return_pct REAL,
            recommended INTEGER,
            reason TEXT,
            created_at TEXT,
            PRIMARY KEY(market_date,ticker,audit_type)
        )
    """)
    con.commit()
    con.close()


def run_precision_audit(market_date=None):
    """Post-close audit: winners missed and recommendations that finished red."""
    try:
        import app
        if not bool(app.CFGD.get("precision_audit_enabled", True)):
            return {"status": "DISABLED", "rows": 0}
        target = str(market_date or app.state_get("last_intraday_market_date") or "")
        if not target:
            return {"status": "NO_MARKET_DATE", "rows": 0}
        con = _dbcon()
        prices = pd.read_sql_query(
            "SELECT ticker,open,close FROM prices WHERE date=?",
            con,
            params=(target,),
        )
        try:
            rec = set(pd.read_sql_query(
                "SELECT DISTINCT ticker FROM recommendation_history WHERE market_date=?",
                con,
                params=(target,),
            )["ticker"].astype(str).map(_ticker).tolist())
        except Exception:
            rec = set()
        if prices.empty:
            con.close()
            return {"status": "NO_PRICES", "rows": 0}
        prices["open"] = pd.to_numeric(prices["open"], errors="coerce")
        prices["close"] = pd.to_numeric(prices["close"], errors="coerce")
        prices = prices.dropna(subset=["open", "close"])
        prices = prices[prices["open"] > 0]
        prices["ret"] = (prices["close"] / prices["open"] - 1.0) * 100.0
        winner_cut = float(app.CFGD.get("precision_audit_winner_pct", 2.0))
        false_cut = float(app.CFGD.get("precision_audit_false_positive_pct", -1.0))
        now = datetime.now(timezone.utc).isoformat()
        rows = 0
        for _, r in prices.iterrows():
            t = _ticker(r.ticker)
            ret = float(r.ret)
            if ret >= winner_cut and t not in rec:
                con.execute(
                    "INSERT OR REPLACE INTO precision_daily_audit VALUES(?,?,?,?,?,?,?)",
                    (target, t, "MISSED_WINNER", ret, 0, "Green mover not present in recommendation history", now),
                )
                rows += 1
            if ret <= false_cut and t in rec:
                con.execute(
                    "INSERT OR REPLACE INTO precision_daily_audit VALUES(?,?,?,?,?,?,?)",
                    (target, t, "FALSE_POSITIVE", ret, 1, "Recommended ticker finished materially red", now),
                )
                rows += 1
        con.commit()
        con.close()
        app.state_set("precision_audit_at", now)
        app.state_set("precision_audit_market_date", target)
        app.state_set("precision_audit_rows", rows)
        return {"status": "READY", "rows": rows, "market_date": target}
    except Exception as exc:
        return {"status": "ERROR", "error": repr(exc), "rows": 0}


def _audit_loop():
    while True:
        try:
            import app
            import market_session
            s = market_session.session_status(app.CFGD)
            status = str(s.get("status") or "").upper()
            if status == "CLOSED":
                target = str(s.get("market_date") or app.state_get("last_intraday_market_date") or "")
                if target and str(app.state_get("precision_audit_market_date") or "") != target:
                    run_precision_audit(target)
            time.sleep(300)
        except Exception:
            time.sleep(300)


def install_core():
    global _INSTALLED, _ORIGINAL_SCORE, _ORIGINAL_STATE, _ORIGINAL_TIME_SIGNAL, _ORIGINAL_UPDATE, _AUDIT_THREAD_STARTED
    if _INSTALLED:
        _patch_worker_priority()
        return
    import dynamic_engine
    _ORIGINAL_SCORE = dynamic_engine._score
    _ORIGINAL_STATE = dynamic_engine._state
    _ORIGINAL_TIME_SIGNAL = dynamic_engine._cached_time_signal
    _ORIGINAL_UPDATE = dynamic_engine.update
    dynamic_engine._cached_time_signal = _patched_time_signal
    dynamic_engine._score = _patched_score
    dynamic_engine._state = _patched_state
    dynamic_engine.update = _patched_update
    _INSTALLED = True
    _patch_worker_priority()
    try:
        _ensure_audit_table()
    except Exception:
        pass
    if os.environ.get("AI_ENGINE_WORKER") == "1" and not _AUDIT_THREAD_STARTED:
        threading.Thread(target=_audit_loop, name="asi-precision-audit", daemon=True).start()
        _AUDIT_THREAD_STARTED = True
