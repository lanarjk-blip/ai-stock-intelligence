import os
import json
import time
import sqlite3
import traceback
from datetime import datetime, timezone, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed

os.environ.setdefault("STREAMLIT_SERVER_HEADLESS", "true")
os.environ["AI_ENGINE_WORKER"] = "1"

import pandas as pd
import app
import dynamic_engine
import recommendation_history
import top5_timeline
import premarket_intelligence
import premarket_evaluation
import premarket_explain
import closing_overnight
import market_session


BASE = app.BASE
CFG = app.CFGD
DB_DIR = app.DB_DIR


# ============================================================
# DATABASE
# ============================================================

def dbcon():
    con = sqlite3.connect(
        app.DB,
        check_same_thread=False,
        timeout=60,
    )

    try:
        con.execute("PRAGMA journal_mode=WAL")
        con.execute("PRAGMA busy_timeout=60000")
    except Exception:
        pass

    return con


def ensure_tables():
    con = dbcon()

    con.execute("""
        CREATE TABLE IF NOT EXISTS engine_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts TEXT,
            phase TEXT,
            status TEXT,
            detail TEXT
        )
    """)

    con.commit()
    con.close()

    try:
        dynamic_engine.ensure_tables()
    except Exception as exc:
        log("DYNAMIC", "TABLE_ERROR", str(exc))

    try:
        recommendation_history.ensure_tables(app.DB)
    except Exception as exc:
        log("HISTORY", "TABLE_ERROR", str(exc))


def log(phase, status, detail=""):
    now = datetime.now(timezone.utc).isoformat()

    try:
        con = dbcon()
        con.execute(
            """
            INSERT INTO engine_log(ts,phase,status,detail)
            VALUES(?,?,?,?)
            """,
            (
                now,
                str(phase),
                str(status),
                str(detail)[:2000],
            ),
        )
        con.commit()
        con.close()
    except Exception:
        pass

    print(
        f"[{now}] {phase}: {status} {detail}",
        flush=True,
    )


# ============================================================
# STATE
# ============================================================

def get_last(key):
    return app.state_get(key)


def stale(key, hours):
    value = get_last(key)

    if not value:
        return True

    try:
        dt = datetime.fromisoformat(
            str(value).replace("Z", "+00:00")
        )

        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)

        age = (
            datetime.now(timezone.utc)
            - dt.astimezone(timezone.utc)
        )

        return age > timedelta(hours=float(hours))

    except Exception:
        return True


# ============================================================
# DATA COLLECTION
# ============================================================

def _recover_dataframe(value):
    """Normalize fetch_chart_recover output to a DataFrame.

    The recovery API currently returns (dataframe, status, symbol, error),
    while older/future implementations may return a DataFrame directly.
    Never allow tuple metadata to reach save_daily()/DataFrame.empty.
    """
    if isinstance(value, pd.DataFrame):
        return value
    if isinstance(value, tuple):
        for item in value:
            if isinstance(item, pd.DataFrame):
                return item
    raise TypeError(
        f"fetch_chart_recover returned no DataFrame: {type(value).__name__}"
    )

def fetch_one(ticker):
    try:
        # V1.8.49: use the resilient collector path when available.
        # It can resolve provider symbol changes and reuse valid cached data.
        recover = getattr(app, "fetch_chart_recover", None)
        if callable(recover):
            recovered = recover(
                ticker,
                CFG["daily_period_days"],
                CFG["daily_interval"],
            )
            df = _recover_dataframe(recovered)
        else:
            df = app.fetch_chart(
                ticker,
                CFG["daily_period_days"],
                CFG["daily_interval"],
            )

        rows = app.save_daily(df, ticker)

        return ticker, bool(not df.empty), rows, ""

    except Exception as exc:
        return ticker, False, 0, str(exc)[:300]


def collect_fast(universe):
    if universe is None or universe.empty:
        return 0, 0, ["Universe kosong"]

    tickers = (
        universe["ticker"]
        .astype(str)
        .dropna()
        .drop_duplicates()
        .tolist()
    )

    ok = 0
    rows = 0
    errors = []

    requested_workers = int(
        CFG.get("collector_workers", 6)
    )

    workers = max(
        1,
        min(requested_workers, len(tickers)),
    )

    with ThreadPoolExecutor(
        max_workers=workers
    ) as executor:

        futures = [
            executor.submit(fetch_one, ticker)
            for ticker in tickers
        ]

        for future in as_completed(futures):
            try:
                ticker, good, count, err = future.result()

                ok += int(good)
                rows += int(count or 0)

                if err:
                    errors.append(
                        f"{ticker}: {err}"
                    )

            except Exception as exc:
                errors.append(str(exc)[:300])

    now = datetime.now(timezone.utc).isoformat()

    app.state_set("last_collection", now)
    app.state_set("last_collection_ok", ok)
    app.state_set("last_collection_rows", rows)
    app.state_set(
        "last_collection_errors",
        len(errors),
    )

    return ok, rows, errors


# ============================================================
# IHSG / MARKET DATA
# ============================================================

def cached_ihsg_available():
    """Return True when a previously persisted IHSG row is available."""
    try:
        con = dbcon()
        row = con.execute(
            "SELECT COUNT(*) FROM market_index WHERE symbol=?",
            ("IHSG",),
        ).fetchone()
        con.close()
        return bool(row and int(row[0]) > 0)
    except Exception:
        return False

def refresh_market_data():
    """
    Model V1.8 menggunakan market features.
    Worker mencoba memperbarui IHSG sebelum training.

    Dibuat kompatibel dengan beberapa versi app.py.
    Jika fungsi IHSG berbeda/tidak tersedia, worker utama
    tetap berjalan.
    """

    log(
        "MARKET_DATA",
        "START",
        "refreshing IHSG / market features",
    )

    try:
        if not hasattr(app, "fetch_ihsg"):
            log(
                "MARKET_DATA",
                "SKIP",
                "app.fetch_ihsg not available",
            )
            return False

        data = app.fetch_ihsg()

        if data is None:
            if cached_ihsg_available():
                log("MARKET_DATA", "CACHE_FALLBACK", "fetch_ihsg returned None; keeping persisted IHSG context")
                return True
            log("MARKET_DATA", "SKIP", "fetch_ihsg returned None and no persisted IHSG context exists")
            return False

        if hasattr(data, "empty") and data.empty:
            if cached_ihsg_available():
                log("MARKET_DATA", "CACHE_FALLBACK", "IHSG provider empty; keeping persisted IHSG context")
                return True
            log("MARKET_DATA", "SKIP", "IHSG dataframe empty and no persisted IHSG context exists")
            return False

        if hasattr(app, "save_ihsg"):
            try:
                app.save_ihsg(data)
            except TypeError:
                # Be tolerant if current app.py persists
                # market data inside fetch_ihsg().
                pass

        app.state_set(
            "market_data_updated_at",
            datetime.now(timezone.utc).isoformat(),
        )

        log(
            "MARKET_DATA",
            "DONE",
            "IHSG refresh completed",
        )

        return True

    except Exception:
        detail = traceback.format_exc()

        log(
            "MARKET_DATA",
            "ERROR",
            detail[-1800:],
        )

        return False


# ============================================================
# UNIVERSE PRIORITY
# ============================================================

def prioritize_universe(universe):
    try:
        seed = pd.read_csv(app.SEED)

        seed["ticker"] = (
            seed["ticker"]
            .astype(str)
            .str.upper()
        )

        seed_tickers = set(seed["ticker"])

        u = universe.copy()

        u["ticker"] = (
            u["ticker"]
            .astype(str)
            .str.upper()
        )

        priority = u[
            u["ticker"].isin(seed_tickers)
        ].copy()

        remainder = u[
            ~u["ticker"].isin(seed_tickers)
        ].copy()

        return (
            pd.concat(
                [priority, remainder],
                ignore_index=True,
            )
            .drop_duplicates("ticker")
            .reset_index(drop=True)
        )

    except FileNotFoundError:
        # Seed is only an optimization. The canonical universe remains valid.
        log(
            "UNIVERSE",
            "PRIORITY_SKIP",
            "universe_seed.csv unavailable; using canonical universe",
        )
        return universe

    except Exception as exc:
        log(
            "UNIVERSE",
            "PRIORITY_FALLBACK",
            str(exc),
        )

        return universe


# ============================================================
# V1.8 OPPORTUNITY SNAPSHOT
# ============================================================

def save_opportunities(res, source):
    if res is None or res.empty:
        return 0

    out = res.copy()

    # Keep decision quality first when available.
    sort_cols = []
    ascending = []

    if "decision_priority" in out.columns:
        sort_cols.append("decision_priority")
        ascending.append(True)

    if "decision_score" in out.columns:
        sort_cols.append("decision_score")
        ascending.append(False)

    if "relative_rank_score" in out.columns:
        sort_cols.append("relative_rank_score")
        ascending.append(False)

    if "ai_score" in out.columns:
        sort_cols.append("ai_score")
        ascending.append(False)

    if sort_cols:
        out = out.sort_values(
            sort_cols,
            ascending=ascending,
        ).reset_index(drop=True)
    else:
        out = out.reset_index(drop=True)

    out["rank"] = range(
        1,
        len(out) + 1
    )

    now = datetime.now(
        timezone.utc
    ).isoformat()

    out["updated_at"] = now

    con = dbcon()

    try:
        out.to_sql(
            "opportunities",
            con,
            if_exists="replace",
            index=False,
        )

        con.commit()

    finally:
        con.close()

    app.state_set(
        "snapshot_ready_at",
        now,
    )

    app.state_set(
        "snapshot_source",
        source,
    )

    app.state_set(
        "snapshot_count",
        len(out),
    )

    return len(out)


# ============================================================
# ALERT SNAPSHOT
# ============================================================

def safe_number(value):
    try:
        if pd.isna(value):
            return None

        return round(
            float(value),
            6,
        )

    except Exception:
        return None


def write_alerts(res):
    if res is None or res.empty:
        return

    top = res.head(
        int(
            CFG.get(
                "max_opportunities",
                10,
            )
        )
    ).copy()

    payload = {
        "generated_at":
            datetime.now(
                timezone.utc
            ).isoformat(),

        "count":
            len(top),

        "opportunities":
            [],
    }

    for _, row in top.iterrows():

        item = {
            "ticker":
                str(
                    row.get(
                        "ticker",
                        "",
                    )
                ),

            "company":
                str(
                    row.get(
                        "company",
                        "",
                    )
                ),

            "ai_score":
                safe_number(
                    row.get("ai_score")
                ),

            "p_up":
                safe_number(
                    row.get("jst_p_up")
                ),

            "signal":
                str(
                    row.get(
                        "jst_signal",
                        "",
                    )
                ),

            "decision":
                str(
                    row.get(
                        "decision",
                        "",
                    )
                ),

            "risk":
                str(
                    row.get(
                        "risk",
                        "",
                    )
                ),

            "close":
                safe_number(
                    row.get("close")
                ),

            "stop_loss":
                safe_number(
                    row.get("stop_loss")
                ),

            "target_1":
                safe_number(
                    row.get("target_1")
                ),

            "target_2":
                safe_number(
                    row.get("target_2")
                ),
        }

        payload[
            "opportunities"
        ].append(item)

    path = os.path.join(
        DB_DIR,
        "latest_alerts.json",
    )

    with open(
        path,
        "w",
        encoding="utf-8",
    ) as file:

        json.dump(
            payload,
            file,
            ensure_ascii=False,
            indent=2,
        )

    app.state_set(
        "last_alert_generation",
        datetime.now(
            timezone.utc
        ).isoformat(),
    )


# ============================================================
# MODEL
# ============================================================

def load_model():
    """
    Prefer deployed V1.8 model.
    Fall back to legacy JST loader.
    """

    try:
        model = app.load_active_model()

        if model is not None:
            return model

    except Exception:
        pass

    try:
        return app.load_jst()

    except Exception:
        return None


def count_price_rows():
    try:
        con = dbcon()

        row = con.execute(
            "SELECT COUNT(*) FROM prices"
        ).fetchone()

        con.close()

        return int(
            row[0] if row else 0
        )

    except Exception:
        return 0


def count_ready_symbols(min_rows=60):
    try:
        con = dbcon()

        row = con.execute(
            """
            SELECT COUNT(*)
            FROM (
                SELECT ticker
                FROM prices
                GROUP BY ticker
                HAVING COUNT(*) >= ?
            )
            """,
            (int(min_rows),),
        ).fetchone()

        con.close()

        return int(
            row[0] if row else 0
        )

    except Exception:
        return 0


def maybe_train(universe, force=False):
    hours = float(
        CFG.get(
            "jst_retrain_hours",
            24,
        )
    )

    existing_model = load_model()

    if (
        not force
        and existing_model is not None
        and not stale(
            "jst_trained_at",
            hours,
        )
    ):
        log(
            "MODEL",
            "SKIP",
            "active model still fresh",
        )

        return "SKIP"

    if universe is None or universe.empty:
        log(
            "MODEL",
            "SKIP",
            "empty universe",
        )

        return "SKIP"

    price_rows = count_price_rows()
    ready_symbols = count_ready_symbols(60)

    log(
        "MODEL",
        "DATA_CHECK",
        (
            f"price_rows={price_rows} "
            f"ready_symbols={ready_symbols} "
            f"universe={len(universe)}"
        ),
    )

    # app.py training itself remains the final authority.
    # These checks only prevent obviously premature attempts.
    if price_rows < 500:
        log(
            "MODEL",
            "SKIP",
            f"not enough stored prices: {price_rows}",
        )

        return "SKIP"

    if ready_symbols < 3:
        log(
            "MODEL",
            "SKIP",
            (
                "not enough symbols with "
                ">=60 price rows"
            ),
        )

        return "SKIP"

    log(
        "MODEL",
        "START",
        (
            f"training symbols={len(universe)} "
            f"price_rows={price_rows} "
            f"ready_symbols={ready_symbols}"
        ),
    )

    try:
        model, rows, accuracy = (
            app.train_jst(universe)
        )

        if model is None:
            log(
                "MODEL",
                "SKIP",
                (
                    "train_jst returned no model; "
                    f"training_rows={rows}"
                ),
            )

            return "SKIP"

        now = datetime.now(
            timezone.utc
        ).isoformat()

        # Keep worker-side state as additional diagnostics.
        # app.train_jst() remains responsible for its model files.
        app.state_set(
            "jst_trained_at",
            now,
        )

        app.state_set(
            "jst_training_rows",
            rows,
        )

        if accuracy is not None:
            app.state_set(
                "jst_accuracy",
                accuracy,
            )

        log(
            "MODEL",
            "DONE",
            (
                f"rows={rows} "
                f"accuracy={accuracy}"
            ),
        )

        return "DONE"

    except Exception:
        detail = traceback.format_exc()

        log(
            "MODEL",
            "ERROR",
            detail[-1800:],
        )

        return "ERROR"


def maybe_walk_forward(
    universe,
    force=False,
):
    days = float(
        CFG.get(
            "walk_forward_days",
            7,
        )
    )

    if (
        not force
        and not stale(
            "walk_forward_at",
            days * 24,
        )
    ):
        return "SKIP"

    model = load_model()

    if model is None:
        log(
            "WALK_FORWARD",
            "SKIP",
            "no active model yet",
        )

        return "SKIP"

    log(
        "WALK_FORWARD",
        "START",
        "scheduled validation",
    )

    try:
        out, mean = (
            app.walk_forward_validation(
                universe,
                int(
                    CFG.get(
                        "walk_forward_folds",
                        2,
                    )
                ),
            )
        )

        now = datetime.now(
            timezone.utc
        ).isoformat()

        app.state_set(
            "walk_forward_at",
            now,
        )

        if mean is not None:
            app.state_set(
                "walk_forward_accuracy",
                mean,
            )

        log(
            "WALK_FORWARD",
            "DONE",
            (
                f"folds={len(out)} "
                f"mean={mean}"
            ),
        )

        return "DONE"

    except Exception:
        detail = traceback.format_exc()

        log(
            "WALK_FORWARD",
            "ERROR",
            detail[-1800:],
        )

        return "ERROR"


# ============================================================
# ANALYSIS + DYNAMIC OPPORTUNITY
# ============================================================

def analyze_and_publish(
    universe,
    source,
    model=None,
):
    if universe is None or universe.empty:
        return pd.DataFrame()

    log(
        "SCAN",
        "START",
        f"{source} symbols={len(universe)}",
    )

    try:
        res = app.analyze_all(
            universe,
            model,
        )

    except TypeError:
        res = app.analyze_all(
            universe
        )

    if res is None or res.empty:

        log(
            "SCAN",
            "EMPTY",
            source,
        )

        return pd.DataFrame()

    saved = save_opportunities(
        res,
        source,
    )

    write_alerts(res)

    dynamic = pd.DataFrame()
    try:
        dynamic = dynamic_engine.update(
            res,
            phase=source.upper(),
        )

        dynamic_count = (
            0
            if dynamic is None
            else len(dynamic)
        )

        log(
            "DYNAMIC",
            "UPDATED",
            f"top={dynamic_count}",
        )

    except Exception as exc:
        log(
            "DYNAMIC",
            "ERROR",
            str(exc),
        )

    # V1.8.42: record real full-universe Top-5 transitions and dispatch APK FCM.
    # Bootstrap snapshots must not produce false Top-5 changes.
    if str(source).lower() == "full-universe" and dynamic is not None and not dynamic.empty:
        try:
            event_result = top5_timeline.record_top5(app.DB, dynamic, source_phase=source, config=CFG)
            log("TOP5_TIMELINE", event_result.get("status", "UNKNOWN"),
                f"events={event_result.get('events', 0)}")
        except Exception:
            log("TOP5_TIMELINE", "ERROR", traceback.format_exc()[-1800:])
        try:
            delivery_result = top5_timeline.dispatch_fcm(app.DB, CFG)
            log("TOP5_FCM", delivery_result.get("status", "UNKNOWN"),
                f"sent={delivery_result.get('sent', 0)} failed={delivery_result.get('failed', 0)}")
        except Exception:
            log("TOP5_FCM", "ERROR", traceback.format_exc()[-1800:])

    # V1.8.40 foundation: immutable recommendation history + paper portfolio.
    # Only the final full-universe snapshot is locked for evaluation. Bootstrap
    # snapshots remain operational and are deliberately excluded from history.
    if str(source).lower() == "full-universe":
        try:
            rec = recommendation_history.record_snapshot(
                app.DB, dynamic, CFG, source_phase=source
            )
            shadow = recommendation_history.update_shadow_portfolio(
                app.DB, CFG
            )
            app.state_set("recommendation_history_at", datetime.now(timezone.utc).isoformat())
            app.state_set("recommendation_history_last_run_id", rec.get("run_id") or "")
            app.state_set("recommendation_history_last_count", rec.get("recommendations", 0))
            app.state_set("shadow_portfolio_at", datetime.now(timezone.utc).isoformat())
            log(
                "HISTORY",
                "UPDATED",
                f"recommendations={rec.get('recommendations',0)} "
                f"shadow_seeded={rec.get('shadow_seeded',0)} "
                f"shadow_changed={shadow.get('changed',0)}",
            )
        except Exception:
            log("HISTORY", "ERROR", traceback.format_exc()[-1800:])

        # V1.8.41 Stage 2: Pre-Market is separate from live Dynamic Opportunity.
        try:
            pre = lock_premarket_picks(res, source_phase=source)
            app.state_set("premarket_status", pre.get("status", ""))
        except Exception:
            log("PREMARKET", "ERROR", traceback.format_exc()[-1800:])

    # V1.8.44: strict next-session Closing / Overnight Intelligence.
    if str(source).lower() == "full-universe":
        try:
            overnight = closing_overnight.select_overnight(res, CFG)
            app.state_set("overnight_status", overnight.get("status", ""))
            app.state_set("overnight_pick_count", len(overnight.get("picks", [])))
            app.state_set("overnight_reason", overnight.get("reason", ""))
            picks = overnight.get("picks")
            if picks is not None and not picks.empty:
                payload = picks.to_dict(orient="records")
                app.state_set("overnight_picks_json", json.dumps(payload, ensure_ascii=False, default=str))
            else:
                app.state_set("overnight_picks_json", "[]")
            log("OVERNIGHT", overnight.get("status", "UNKNOWN"), f"picks={len(overnight.get('picks', []))}")
        except Exception:
            log("OVERNIGHT", "ERROR", traceback.format_exc()[-1800:])

    top = res.iloc[0]

    log(
        "SCAN",
        "DONE",
        (
            f"source={source} "
            f"analyzed={len(res)} "
            f"saved={saved} "
            f"top={top.get('ticker', '')} "
            f"score={safe_number(top.get('ai_score'))}"
        ),
    )

    return res



# ============================================================
# V1.8.41 STAGE 2 - PRE-MARKET TOP PICKS + NO TRADE
# ============================================================

def _wib_now():
    return datetime.now(timezone.utc) + timedelta(hours=7)

def _pm_num(value, default=None):
    try:
        if value is None or pd.isna(value):
            return default
        return float(value)
    except Exception:
        return default

def ensure_premarket_tables():
    con = dbcon()
    try:
        con.execute("""CREATE TABLE IF NOT EXISTS premarket_picks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            market_date TEXT NOT NULL, generated_at TEXT NOT NULL,
            rank INTEGER, ticker TEXT, company TEXT, decision TEXT,
            confidence REAL, ai_score REAL, risk TEXT,
            entry_low REAL, entry_high REAL, stop_loss REAL,
            target_1 REAL, target_2 REAL, ihsg_regime TEXT,
            reason TEXT, source_phase TEXT,
            UNIQUE(market_date, rank))""")
        con.execute("""CREATE TABLE IF NOT EXISTS premarket_runs (
            market_date TEXT PRIMARY KEY, generated_at TEXT NOT NULL,
            status TEXT NOT NULL, pick_count INTEGER NOT NULL, note TEXT)""")
        con.commit()
    finally:
        con.close()

def premarket_market_date():
    # Session-aware: after the final IDX close a new snapshot belongs to the
    # next valid trading date, never to the session that already ended.
    return market_session.next_trading_date(CFG, now=_wib_now(), after_close=True)

def lock_premarket_picks(res, source_phase="full-universe"):
    ensure_premarket_tables()
    market_date = premarket_market_date()
    generated_at = datetime.now(timezone.utc).isoformat()

    con = dbcon()
    try:
        old = con.execute(
            "SELECT status,pick_count FROM premarket_runs WHERE market_date=?",
            (market_date,)).fetchone()
        if old:
            return {"status":old[0], "pick_count":int(old[1] or 0)}
    finally:
        con.close()

    selection = premarket_intelligence.select_high_conviction(res, CFG)
    c = selection["picks"].copy()

    con = dbcon()
    try:
        if c.empty:
            con.execute("""INSERT OR IGNORE INTO premarket_runs
                (market_date,generated_at,status,pick_count,note)
                VALUES(?,?,?,?,?)""",
                (market_date,generated_at,"NO_TRADE",0,
                 str(selection.get("reason") or "No candidate passed pre-market quality gates.")))
            con.commit()
            log("PREMARKET","NO_TRADE",f"market_date={market_date}")
            return {"status":"NO_TRADE","pick_count":0}

        rows=[]
        for i,row in c.iterrows():
            conf=_pm_num(row.get("confidence"))
            if conf is None: conf=_pm_num(row.get("jst_p_up"))
            if conf is not None and conf <= 1: conf*=100
            close=_pm_num(row.get("close"))
            lo=_pm_num(row.get("entry_low"))
            hi=_pm_num(row.get("entry_high"))
            if lo is None and close is not None: lo=close*0.985
            if hi is None and close is not None: hi=close*1.015
            reason=premarket_explain.explain_pick(row.to_dict())
            rows.append((market_date,generated_at,i+1,str(row.get("ticker","")),
                str(row.get("company","")),str(row.get("decision","WATCH")),
                conf,_pm_num(row.get("ai_score")),str(row.get("risk","")),
                lo,hi,_pm_num(row.get("stop_loss")),_pm_num(row.get("target_1")),
                _pm_num(row.get("target_2")),str(row.get("market_regime","")),
                reason,str(source_phase)))
        con.executemany("""INSERT OR IGNORE INTO premarket_picks
            (market_date,generated_at,rank,ticker,company,decision,confidence,
             ai_score,risk,entry_low,entry_high,stop_loss,target_1,target_2,
             ihsg_regime,reason,source_phase)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", rows)
        con.execute("""INSERT OR IGNORE INTO premarket_runs
            (market_date,generated_at,status,pick_count,note)
            VALUES(?,?,?,?,?)""",
            (market_date,generated_at,"LOCKED",len(rows),
             "Immutable first selection for this market date."))
        con.commit()
    finally:
        con.close()

    app.state_set("premarket_locked_at",generated_at)
    app.state_set("premarket_market_date",market_date)
    app.state_set("premarket_pick_count",len(rows))
    log("PREMARKET","LOCKED",
        f"market_date={market_date} picks={len(rows)} tickers={','.join(r[3] for r in rows)}")
    return {"status":"LOCKED","pick_count":len(rows)}



# ============================================================
# V1.8.46 POST-CLOSE PRE-MARKET EVALUATION
# ============================================================

def ensure_premarket_evaluation_table():
    con=dbcon()
    try:
        con.execute("""CREATE TABLE IF NOT EXISTS premarket_evaluations (
            market_date TEXT NOT NULL, evaluated_at TEXT NOT NULL,
            ticker TEXT NOT NULL, decision TEXT, entry_touched INTEGER,
            stop_hit INTEGER, target_1_hit INTEGER, target_2_hit INTEGER,
            outcome TEXT, verdict TEXT, return_pct REAL,
            session_open REAL, session_high REAL, session_low REAL, session_close REAL,
            PRIMARY KEY(market_date,ticker))""")
        con.commit()
    finally:
        con.close()


def evaluate_locked_premarket():
    """Evaluate completed locked sessions from stored daily OHLC without hindsight edits."""
    ensure_premarket_tables(); ensure_premarket_evaluation_table()
    today=_wib_now().date().isoformat()
    con=dbcon()
    try:
        dates=[r[0] for r in con.execute(
            """SELECT r.market_date FROM premarket_runs r
               WHERE r.status='LOCKED' AND r.market_date < ?
                 AND EXISTS (SELECT 1 FROM premarket_picks p WHERE p.market_date=r.market_date)
                 AND NOT EXISTS (
                     SELECT 1 FROM premarket_picks p2
                     LEFT JOIN premarket_evaluations e
                       ON e.market_date=p2.market_date AND e.ticker=p2.ticker
                     WHERE p2.market_date=r.market_date AND e.ticker IS NULL
                 ) = 0
               ORDER BY r.market_date""",
            (today,)).fetchall()]
    finally:
        con.close()
    total=0
    for market_date in dates:
        con=dbcon()
        try:
            picks=pd.read_sql_query("SELECT * FROM premarket_picks WHERE market_date=? ORDER BY rank",con,params=(market_date,))
        finally:
            con.close()
        if picks.empty: continue
        rows=[]
        for _,pick in picks.iterrows():
            ticker=str(pick.get('ticker','')).upper().replace('.JK','').strip()
            con=dbcon()
            try:
                market=pd.read_sql_query(
                    "SELECT open,high,low,close FROM prices WHERE ticker=? AND date(date)=date(?) ORDER BY date DESC LIMIT 1",
                    con,params=(ticker,market_date))
            except Exception:
                market=pd.DataFrame()
            finally:
                con.close()
            if market.empty: continue
            ev=premarket_evaluation.evaluate_pick(pick.to_dict(),market.iloc[0].to_dict())
            rows.append((market_date,datetime.now(timezone.utc).isoformat(),ticker,ev.get('decision'),int(ev.get('entry_touched',False)),int(ev.get('stop_hit',False)),int(ev.get('target_1_hit',False)),int(ev.get('target_2_hit',False)),ev.get('outcome'),ev.get('verdict'),ev.get('return_pct_vs_entry_low'),ev.get('session_open'),ev.get('session_high'),ev.get('session_low'),ev.get('session_close')))
        if rows:
            con=dbcon()
            try:
                con.executemany("""INSERT OR IGNORE INTO premarket_evaluations
                    (market_date,evaluated_at,ticker,decision,entry_touched,stop_hit,target_1_hit,target_2_hit,outcome,verdict,return_pct,session_open,session_high,session_low,session_close)
                    VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",rows)
                con.commit(); total+=len(rows)
            finally:
                con.close()
    if total:
        app.state_set('premarket_evaluation_at',datetime.now(timezone.utc).isoformat())
        log('PREMARKET_EVAL','UPDATED',f'rows={total}')
    return total


# ============================================================
# ENGINE CYCLE
# ============================================================

def cycle():
    ensure_tables()
    try:
        evaluate_locked_premarket()
    except Exception:
        log("PREMARKET_EVAL","ERROR",traceback.format_exc()[-1800:])

    universe, source = app.load_universe()

    if universe is None or universe.empty:
        raise RuntimeError(
            "Stock universe is empty."
        )

    universe = prioritize_universe(
        universe
    )

    log(
        "ENGINE",
        "START",
        (
            f"version={CFG.get('engine_version', 'unknown')} "
            f"universe={len(universe)} "
            f"source={source}"
        ),
    )

    # ========================================================
    # PHASE 1
    # FAST BOOTSTRAP
    # ========================================================

    bootstrap_n = min(
        int(
            CFG.get(
                "bootstrap_symbols",
                30,
            )
        ),
        len(universe),
    )

    bootstrap = (
        universe
        .head(bootstrap_n)
        .copy()
    )

    log(
        "BOOTSTRAP",
        "START",
        f"symbols={len(bootstrap)}",
    )

    ok, rows, errors = collect_fast(
        bootstrap
    )

    log(
        "BOOTSTRAP",
        "COLLECT_DONE",
        (
            f"ok={ok}/{len(bootstrap)} "
            f"rows={rows} "
            f"errors={len(errors)}"
        ),
    )

    # Produce READY quickly, even before model training.
    # V1.8.42 diagnostic: expose a small sample of real provider failures.
    if errors:
        log(
            "BOOTSTRAP",
            "ERROR_SAMPLE",
            " | ".join(str(err)[:350] for err in errors[:5]),
        )

    bootstrap_res = analyze_and_publish(
        bootstrap,
        "technical-bootstrap",
        model=None,
    )

    if not bootstrap_res.empty:
        log(
            "BOOTSTRAP",
            "SNAPSHOT_READY",
            f"count={len(bootstrap_res)}",
        )

    # ========================================================
    # PHASE 2
    # MARKET DATA + EARLY MODEL ATTEMPT
    # ========================================================

    refresh_market_data()

    maybe_train(
        bootstrap,
        force=False,
    )

    model = load_model()

    if model is not None:
        refined = analyze_and_publish(
            bootstrap,
            "model-refined-bootstrap",
            model=model,
        )

        if not refined.empty:
            log(
                "MODEL_REFINE",
                "SNAPSHOT_UPDATED",
                f"count={len(refined)}",
            )

    # ========================================================
    # PHASE 3
    # FULL UNIVERSE COLLECTION
    # ========================================================

    log(
        "FULL_COLLECT",
        "START",
        f"symbols={len(universe)}",
    )

    ok, rows, errors = collect_fast(
        universe
    )

    log(
        "FULL_COLLECT",
        "DONE",
        (
            f"ok={ok}/{len(universe)} "
            f"rows={rows} "
            f"errors={len(errors)}"
        ),
    )

    # V1.8.42 diagnostic: expose a small sample of real provider failures.
    if errors:
        log(
            "FULL_COLLECT",
            "ERROR_SAMPLE",
            " | ".join(str(err)[:350] for err in errors[:5]),
        )

    # ========================================================
    # PHASE 4
    # FINAL MODEL TRAINING
    # ========================================================

    refresh_market_data()

    # Force another attempt because the full collection may
    # have supplied the data that bootstrap did not have.
    maybe_train(
        universe,
        force=True,
    )

    model = load_model()

    # Walk-forward only makes sense after a model exists.
    if model is not None:
        maybe_walk_forward(
            universe,
            force=False,
        )
    else:
        log(
            "MODEL",
            "BACKGROUND",
            (
                "No deployable model after full collection. "
                "Technical snapshot remains available."
            ),
        )

    # ========================================================
    # PHASE 5
    # FULL V1.8 SNAPSHOT
    # ========================================================

    result = analyze_and_publish(
        universe,
        "full-universe",
        model=model,
    )

    now = datetime.now(
        timezone.utc
    ).isoformat()

    app.state_set(
        "engine_last_run",
        now,
    )

    app.state_set(
        "engine_version",
        CFG.get(
            "engine_version",
            "1.8.33",
        ),
    )

    app.state_set(
        "engine_model_status",
        (
            "ACTIVE"
            if model is not None
            else "BACKGROUND"
        ),
    )

    log(
        "ENGINE",
        "DONE",
        (
            f"results={len(result)} "
            f"model={'ACTIVE' if model is not None else 'BACKGROUND'}"
        ),
    )

    return result


# ============================================================
# WORKER LOOP
# ============================================================

def run_forever():
    interval = max(
        5,
        int(
            CFG.get(
                "worker_cycle_minutes",
                30,
            )
        ),
    ) * 60

    print(
        (
            "AI Stock Intelligence "
            f"V{CFG.get('engine_version', '1.8.33')} "
            "background engine started."
        ),
        flush=True,
    )

    while True:

        started = time.time()

        try:
            cycle()

        except Exception:
            detail = traceback.format_exc()

            print(
                detail,
                flush=True,
            )

            try:
                log(
                    "ENGINE",
                    "ERROR",
                    detail[-1800:],
                )
            except Exception:
                pass

        elapsed = (
            time.time()
            - started
        )

        sleep_for = max(
            30,
            interval - elapsed,
        )

        print(
            (
                "Next engine cycle in "
                f"{sleep_for / 60:.1f} min."
            ),
            flush=True,
        )

        time.sleep(
            sleep_for
        )


if __name__ == "__main__":
    run_forever()
