import sqlite3
import hashlib
from datetime import datetime, timezone

import numpy as np
import pandas as pd


SCHEMA_VERSION = "1.8.40"


def _con(db_path):
    con = sqlite3.connect(db_path, check_same_thread=False, timeout=60)
    try:
        con.execute("PRAGMA journal_mode=WAL")
        con.execute("PRAGMA busy_timeout=60000")
        con.execute("PRAGMA synchronous=NORMAL")
    except Exception:
        pass
    return con


def _f(value):
    try:
        if value is None or pd.isna(value):
            return None
        return float(value)
    except Exception:
        return None


def _s(value, default=""):
    if value is None:
        return default
    try:
        if pd.isna(value):
            return default
    except Exception:
        pass
    return str(value)


def ensure_tables(db_path):
    con = _con(db_path)
    con.execute(
        """CREATE TABLE IF NOT EXISTS recommendation_runs (
            run_id TEXT PRIMARY KEY,
            generated_at TEXT NOT NULL,
            market_date TEXT NOT NULL,
            source_phase TEXT,
            market_regime TEXT,
            candidate_count INTEGER,
            locked INTEGER DEFAULT 1,
            schema_version TEXT
        )"""
    )
    con.execute(
        """CREATE TABLE IF NOT EXISTS recommendation_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id TEXT NOT NULL,
            generated_at TEXT NOT NULL,
            market_date TEXT NOT NULL,
            ticker TEXT NOT NULL,
            company TEXT,
            rank INTEGER,
            recommendation TEXT,
            candidate_mode TEXT,
            opportunity_state TEXT,
            entry_status TEXT,
            opportunity_score REAL,
            ai_score REAL,
            p_up REAL,
            p_down REAL,
            confidence REAL,
            reference_price REAL,
            entry_low REAL,
            entry_high REAL,
            stop_loss REAL,
            target_1 REAL,
            target_2 REAL,
            risk_reward REAL,
            risk TEXT,
            market_regime TEXT,
            reason TEXT,
            outcome_status TEXT DEFAULT 'PENDING',
            outcome_return_pct REAL,
            outcome_at TEXT,
            UNIQUE(run_id, ticker)
        )"""
    )
    con.execute(
        """CREATE TABLE IF NOT EXISTS shadow_positions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            recommendation_id INTEGER UNIQUE,
            run_id TEXT,
            ticker TEXT NOT NULL,
            company TEXT,
            recommended_at TEXT NOT NULL,
            recommendation_date TEXT NOT NULL,
            rank INTEGER,
            status TEXT DEFAULT 'WAITING_ENTRY',
            entry_low REAL,
            entry_high REAL,
            planned_stop REAL,
            planned_target_1 REAL,
            planned_target_2 REAL,
            entry_at TEXT,
            entry_price REAL,
            tp1_hit_at TEXT,
            last_price REAL,
            last_updated TEXT,
            max_favorable_pct REAL DEFAULT 0,
            max_adverse_pct REAL DEFAULT 0,
            closed_at TEXT,
            exit_price REAL,
            return_pct REAL,
            exit_reason TEXT,
            ambiguity_note TEXT
        )"""
    )
    existing = {str(r[1]) for r in con.execute("PRAGMA table_info(recommendation_history)").fetchall()}
    if "candidate_mode" not in existing:
        con.execute("ALTER TABLE recommendation_history ADD COLUMN candidate_mode TEXT")
    existing = {str(r[1]) for r in con.execute("PRAGMA table_info(recommendation_history)").fetchall()}
    if "candidate_mode" not in existing:
        con.execute("ALTER TABLE recommendation_history ADD COLUMN candidate_mode TEXT")
    con.execute("CREATE INDEX IF NOT EXISTS idx_rec_hist_ticker_date ON recommendation_history(ticker, market_date)")
    con.execute("CREATE INDEX IF NOT EXISTS idx_shadow_status_ticker ON shadow_positions(status, ticker)")
    con.commit()
    con.close()


def _market_date(now=None):
    # Recommendation storage uses WIB trading date while timestamps remain UTC.
    now = now or datetime.now(timezone.utc)
    try:
        from zoneinfo import ZoneInfo
        return now.astimezone(ZoneInfo("Asia/Jakarta")).date().isoformat()
    except Exception:
        return now.date().isoformat()


def _run_id(dynamic_df, source_phase, generated_at, market_date):
    tickers = []
    if dynamic_df is not None and not dynamic_df.empty and "ticker" in dynamic_df.columns:
        tickers = dynamic_df.sort_values("rank")["ticker"].astype(str).tolist() if "rank" in dynamic_df.columns else dynamic_df["ticker"].astype(str).tolist()
    raw = f"{market_date}|{source_phase}|{generated_at}|{'|'.join(tickers)}"
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:20]


def record_snapshot(db_path, dynamic_df, cfg=None, source_phase="full-universe"):
    """Persist an immutable recommendation snapshot and seed paper positions.

    This function deliberately records only the supplied snapshot. It does not
    rewrite older recommendations when later rankings change.
    """
    ensure_tables(db_path)
    if dynamic_df is None or dynamic_df.empty:
        return {"run_id": None, "recommendations": 0, "shadow_seeded": 0}

    cfg = cfg or {}
    now = datetime.now(timezone.utc)
    generated_at = now.isoformat()
    mdate = _market_date(now)
    run_id = _run_id(dynamic_df, source_phase, generated_at, mdate)
    market_regime = _s(dynamic_df.iloc[0].get("market_regime", "UNKNOWN"), "UNKNOWN")

    con = _con(db_path)
    seeded = 0
    inserted = 0
    try:
        con.execute(
            "INSERT OR IGNORE INTO recommendation_runs(run_id,generated_at,market_date,source_phase,market_regime,candidate_count,locked,schema_version) VALUES(?,?,?,?,?,?,1,?)",
            (run_id, generated_at, mdate, str(source_phase), market_regime, int(len(dynamic_df)), SCHEMA_VERSION),
        )

        for i, row in dynamic_df.sort_values("rank" if "rank" in dynamic_df.columns else dynamic_df.columns[0]).iterrows():
            rank = int(row.get("rank", i + 1))
            ticker = _s(row.get("ticker")).upper().replace(".JK", "")
            if not ticker:
                continue
            recommendation = _s(row.get("decision", row.get("state", "WATCH")), "WATCH")
            candidate_mode = _s(row.get("candidate_mode"), "STRICT").upper()
            vals = (
                run_id, generated_at, mdate, ticker, _s(row.get("company")), rank,
                recommendation, candidate_mode, _s(row.get("state")), _s(row.get("entry_status")),
                _f(row.get("opportunity_score")), _f(row.get("ai_score")),
                _f(row.get("p_up")), _f(row.get("p_down")), _f(row.get("confidence")),
                _f(row.get("close")), _f(row.get("entry_low")), _f(row.get("entry_high")),
                _f(row.get("stop_loss")), _f(row.get("target_1")), _f(row.get("target_2")),
                _f(row.get("risk_reward")), _s(row.get("risk")), _s(row.get("market_regime")),
                _s(row.get("reason")),
            )
            cur = con.execute(
                """INSERT OR IGNORE INTO recommendation_history(
                    run_id,generated_at,market_date,ticker,company,rank,recommendation,candidate_mode,
                    opportunity_state,entry_status,opportunity_score,ai_score,p_up,p_down,confidence,
                    reference_price,entry_low,entry_high,stop_loss,target_1,target_2,risk_reward,risk,
                    market_regime,reason
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                vals,
            )
            if cur.rowcount <= 0:
                continue
            inserted += 1
            rec_id = int(cur.lastrowid)

            actionable = (
                candidate_mode != "MONITOR_FALLBACK"
                and recommendation.upper() in {"BUY", "STRONG_BUY"}
            )
            if not actionable:
                continue

            # One live paper position per ticker. New recommendations remain in
            # history, but do not pyramid the same ticker automatically.
            active = con.execute(
                "SELECT id FROM shadow_positions WHERE ticker=? AND status IN ('WAITING_ENTRY','OPEN','TP1_HIT') LIMIT 1",
                (ticker,),
            ).fetchone()
            if active:
                continue
            con.execute(
                """INSERT OR IGNORE INTO shadow_positions(
                    recommendation_id,run_id,ticker,company,recommended_at,recommendation_date,rank,status,
                    entry_low,entry_high,planned_stop,planned_target_1,planned_target_2,last_updated
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    rec_id, run_id, ticker, _s(row.get("company")), generated_at, mdate, rank,
                    "WAITING_ENTRY", _f(row.get("entry_low")), _f(row.get("entry_high")),
                    _f(row.get("stop_loss")), _f(row.get("target_1")), _f(row.get("target_2")), generated_at,
                ),
            )
            seeded += 1
        con.commit()
    finally:
        con.close()
    return {"run_id": run_id, "recommendations": inserted, "shadow_seeded": seeded}


def _bars_after(con, ticker, date_text):
    d = pd.read_sql_query(
        "SELECT date,open,high,low,close FROM prices WHERE ticker=? AND date>? ORDER BY date",
        con,
        params=(ticker, date_text),
    )
    if d.empty:
        return d
    for c in ["open", "high", "low", "close"]:
        d[c] = pd.to_numeric(d[c], errors="coerce")
    return d.dropna(subset=["date", "close"])


def _choose_entry(bar, lo, hi):
    if lo is None and hi is None:
        return _f(bar.close)
    if lo is None:
        lo = hi
    if hi is None:
        hi = lo
    lo, hi = sorted([float(lo), float(hi)])
    o, h, l = _f(bar.open), _f(bar.high), _f(bar.low)
    if o is not None and lo <= o <= hi:
        return o
    if h is not None and l is not None and h >= lo and l <= hi:
        # Conservative fill inside the planned zone for a long paper trade.
        return hi
    return None


def _ret(exit_price, entry_price):
    if entry_price is None or not entry_price:
        return None
    return (float(exit_price) / float(entry_price) - 1.0) * 100.0


def update_shadow_portfolio(db_path, cfg=None):
    """Advance paper positions using future daily OHLC only.

    To avoid look-ahead, the recommendation day itself is never used for an
    entry or outcome. If a daily bar touches both stop and target after entry,
    the event is marked AMBIGUOUS rather than pretending to know intraday order.
    """
    ensure_tables(db_path)
    cfg = cfg or {}
    entry_expiry = int(cfg.get("shadow_entry_expiry_days", 2))
    max_hold = int(cfg.get("shadow_max_holding_days", 5))
    con = _con(db_path)
    changed = 0
    try:
        positions = pd.read_sql_query(
            "SELECT * FROM shadow_positions WHERE status IN ('WAITING_ENTRY','OPEN','TP1_HIT') ORDER BY id",
            con,
        )
        for _, p in positions.iterrows():
            bars = _bars_after(con, str(p.ticker), str(p.recommendation_date))
            if bars.empty:
                continue
            status = str(p.status)
            entry_price = _f(p.entry_price)
            entry_at = _s(p.entry_at)
            start_idx = 0

            if status == "WAITING_ENTRY":
                found = None
                for idx, bar in bars.head(entry_expiry).iterrows():
                    fill = _choose_entry(bar, _f(p.entry_low), _f(p.entry_high))
                    if fill is not None:
                        found = (idx, bar, fill)
                        break
                if found is None:
                    if len(bars) >= entry_expiry:
                        last = bars.iloc[min(entry_expiry, len(bars)) - 1]
                        con.execute(
                            "UPDATE shadow_positions SET status='EXPIRED_NO_ENTRY',closed_at=?,exit_reason='ENTRY_NOT_TRIGGERED',last_price=?,last_updated=? WHERE id=?",
                            (str(last.date), _f(last.close), datetime.now(timezone.utc).isoformat(), int(p.id)),
                        )
                        con.execute(
                            "UPDATE recommendation_history SET outcome_status='EXPIRED_NO_ENTRY',outcome_at=? WHERE id=?",
                            (str(last.date), int(p.recommendation_id)),
                        )
                        changed += 1
                    continue
                idx, bar, entry_price = found
                entry_at = str(bar.date)
                start_idx = list(bars.index).index(idx)
                status = "OPEN"
                con.execute(
                    "UPDATE shadow_positions SET status='OPEN',entry_at=?,entry_price=?,last_price=?,last_updated=? WHERE id=?",
                    (entry_at, entry_price, _f(bar.close), datetime.now(timezone.utc).isoformat(), int(p.id)),
                )
                changed += 1
            else:
                if entry_at:
                    candidates = bars.index[bars["date"].astype(str) >= entry_at].tolist()
                    start_idx = list(bars.index).index(candidates[0]) if candidates else 0

            post = bars.iloc[start_idx: start_idx + max_hold].copy()
            if post.empty or entry_price is None:
                continue

            stop = _f(p.planned_stop)
            t1 = _f(p.planned_target_1)
            t2 = _f(p.planned_target_2)
            tp1_hit_at = _s(p.tp1_hit_at)
            mfe = _f(p.max_favorable_pct) or 0.0
            mae = _f(p.max_adverse_pct) or 0.0
            closed = False

            for _, bar in post.iterrows():
                high, low, close = _f(bar.high), _f(bar.low), _f(bar.close)
                if high is not None:
                    mfe = max(mfe, (high / entry_price - 1.0) * 100.0)
                if low is not None:
                    mae = min(mae, (low / entry_price - 1.0) * 100.0)

                hit_stop = stop is not None and low is not None and low <= stop
                hit_t2 = t2 is not None and high is not None and high >= t2
                hit_t1 = t1 is not None and high is not None and high >= t1

                if hit_stop and (hit_t1 or hit_t2):
                    con.execute(
                        """UPDATE shadow_positions SET status='AMBIGUOUS',closed_at=?,last_price=?,last_updated=?,
                           max_favorable_pct=?,max_adverse_pct=?,exit_reason='STOP_AND_TARGET_SAME_DAILY_BAR',ambiguity_note=? WHERE id=?""",
                        (str(bar.date), close, datetime.now(timezone.utc).isoformat(), mfe, mae,
                         "Daily OHLC cannot prove whether stop or target happened first.", int(p.id)),
                    )
                    con.execute(
                        "UPDATE recommendation_history SET outcome_status='AMBIGUOUS',outcome_at=? WHERE id=?",
                        (str(bar.date), int(p.recommendation_id)),
                    )
                    changed += 1
                    closed = True
                    break

                if hit_stop:
                    rp = _ret(stop, entry_price)
                    con.execute(
                        """UPDATE shadow_positions SET status='STOP',closed_at=?,exit_price=?,return_pct=?,last_price=?,last_updated=?,
                           max_favorable_pct=?,max_adverse_pct=?,exit_reason='STOP_LOSS' WHERE id=?""",
                        (str(bar.date), stop, rp, close, datetime.now(timezone.utc).isoformat(), mfe, mae, int(p.id)),
                    )
                    con.execute(
                        "UPDATE recommendation_history SET outcome_status='STOP',outcome_return_pct=?,outcome_at=? WHERE id=?",
                        (rp, str(bar.date), int(p.recommendation_id)),
                    )
                    changed += 1
                    closed = True
                    break

                if hit_t2:
                    rp = _ret(t2, entry_price)
                    con.execute(
                        """UPDATE shadow_positions SET status='TP2',tp1_hit_at=COALESCE(tp1_hit_at,?),closed_at=?,exit_price=?,return_pct=?,last_price=?,last_updated=?,
                           max_favorable_pct=?,max_adverse_pct=?,exit_reason='TARGET_2' WHERE id=?""",
                        (str(bar.date), str(bar.date), t2, rp, close, datetime.now(timezone.utc).isoformat(), mfe, mae, int(p.id)),
                    )
                    con.execute(
                        "UPDATE recommendation_history SET outcome_status='TP2',outcome_return_pct=?,outcome_at=? WHERE id=?",
                        (rp, str(bar.date), int(p.recommendation_id)),
                    )
                    changed += 1
                    closed = True
                    break

                if hit_t1 and not tp1_hit_at:
                    tp1_hit_at = str(bar.date)
                    status = "TP1_HIT"
                    con.execute(
                        "UPDATE shadow_positions SET status='TP1_HIT',tp1_hit_at=?,last_price=?,last_updated=?,max_favorable_pct=?,max_adverse_pct=? WHERE id=?",
                        (tp1_hit_at, close, datetime.now(timezone.utc).isoformat(), mfe, mae, int(p.id)),
                    )
                    con.execute(
                        "UPDATE recommendation_history SET outcome_status='TP1_HIT',outcome_at=? WHERE id=?",
                        (tp1_hit_at, int(p.recommendation_id)),
                    )
                    changed += 1

                con.execute(
                    "UPDATE shadow_positions SET last_price=?,last_updated=?,max_favorable_pct=?,max_adverse_pct=? WHERE id=?",
                    (close, datetime.now(timezone.utc).isoformat(), mfe, mae, int(p.id)),
                )

            if not closed and len(post) >= max_hold:
                bar = post.iloc[-1]
                exit_price = _f(bar.close)
                rp = _ret(exit_price, entry_price) if exit_price is not None else None
                con.execute(
                    """UPDATE shadow_positions SET status='EXPIRED',closed_at=?,exit_price=?,return_pct=?,last_price=?,last_updated=?,
                       max_favorable_pct=?,max_adverse_pct=?,exit_reason='MAX_HOLD_DAYS' WHERE id=?""",
                    (str(bar.date), exit_price, rp, exit_price, datetime.now(timezone.utc).isoformat(), mfe, mae, int(p.id)),
                )
                con.execute(
                    "UPDATE recommendation_history SET outcome_status='EXPIRED',outcome_return_pct=?,outcome_at=? WHERE id=?",
                    (rp, str(bar.date), int(p.recommendation_id)),
                )
                changed += 1

        con.commit()
    finally:
        con.close()
    return {"changed": changed}


def read_history(db_path, limit=300):
    ensure_tables(db_path)
    con = _con(db_path)
    try:
        return pd.read_sql_query(
            "SELECT * FROM recommendation_history ORDER BY id DESC LIMIT ?", con, params=(int(limit),)
        )
    finally:
        con.close()


def read_shadow(db_path, limit=300):
    ensure_tables(db_path)
    con = _con(db_path)
    try:
        return pd.read_sql_query(
            "SELECT * FROM shadow_positions ORDER BY id DESC LIMIT ?", con, params=(int(limit),)
        )
    finally:
        con.close()


def performance_summary(db_path):
    d = read_shadow(db_path, 100000)
    if d.empty:
        return {
            "positions": 0, "open": 0, "closed": 0, "wins": 0, "losses": 0,
            "win_rate": None, "avg_return": None, "avg_win": None, "avg_loss": None,
            "profit_factor": None, "max_drawdown": None,
        }
    active = int(d.status.isin(["WAITING_ENTRY", "OPEN", "TP1_HIT"]).sum())
    closed = d[d.status.isin(["TP2", "STOP", "EXPIRED"])].copy()
    closed["return_pct"] = pd.to_numeric(closed["return_pct"], errors="coerce")
    closed = closed.dropna(subset=["return_pct"]).sort_values("id")
    wins = closed[closed.return_pct > 0]
    losses = closed[closed.return_pct < 0]
    wr = (len(wins) / len(closed) * 100.0) if len(closed) else None
    avg_ret = float(closed.return_pct.mean()) if len(closed) else None
    avg_win = float(wins.return_pct.mean()) if len(wins) else None
    avg_loss = float(losses.return_pct.mean()) if len(losses) else None
    gross_win = float(wins.return_pct.sum()) if len(wins) else 0.0
    gross_loss = abs(float(losses.return_pct.sum())) if len(losses) else 0.0
    pf = (gross_win / gross_loss) if gross_loss > 0 else (float("inf") if gross_win > 0 else None)
    max_dd = None
    if len(closed):
        equity = (1.0 + closed.return_pct.values / 100.0).cumprod()
        peak = np.maximum.accumulate(equity)
        dd = (equity / peak - 1.0) * 100.0
        max_dd = float(dd.min()) if len(dd) else None
    return {
        "positions": int(len(d)), "open": active, "closed": int(len(closed)),
        "wins": int(len(wins)), "losses": int(len(losses)), "win_rate": wr,
        "avg_return": avg_ret, "avg_win": avg_win, "avg_loss": avg_loss,
        "profit_factor": pf, "max_drawdown": max_dd,
    }
