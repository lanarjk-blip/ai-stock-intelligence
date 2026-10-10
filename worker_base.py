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


BASE = app.BASE
CFG = app.CFGD
DB_DIR = app.DB_DIR


# ============================================================
# DATABASE
# ============================================================

def dbcon():
    return sqlite3.connect(app.DB, check_same_thread=False)


def ensure_tables():
    con = dbcon()

    # Engine log is owned by the worker.
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

    # Dynamic Opportunity owns its own V1.8.x tables.
    dynamic_engine.ensure_tables()


def log(phase, status, detail=""):
    now = datetime.now(timezone.utc).isoformat()

    try:
        con = dbcon()
        con.execute(
            "INSERT INTO engine_log(ts,phase,status,detail) VALUES(?,?,?,?)",
            (now, str(phase), str(status), str(detail)[:2000]),
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

def fetch_one(ticker):
    try:
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

    tickers = universe["ticker"].astype(str).tolist()

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
    """
    Save the COMPLETE V1.8 analysis dataframe.

    We intentionally do NOT use the old V1.7 fixed
    opportunities schema. app.py V1.8.x produces additional
    fields such as relative rank, percentile, decision,
    risk/reward, market regime, etc.

    The dataframe itself becomes the SQLite schema.
    """

    if res is None or res.empty:
        return 0

    out = res.copy()

    # Main dashboard expects a rank.
    if "relative_rank_score" in out.columns:
        out = out.sort_values(
            ["relative_rank_score", "ai_score"],
            ascending=False,
        ).reset_index(drop=True)

    elif "ai_score" in out.columns:
        out = out.sort_values(
            "ai_score",
            ascending=False,
        ).reset_index(drop=True)

    else:
        out = out.reset_index(drop=True)

    out["rank"] = range(1, len(out) + 1)

    now = datetime.now(timezone.utc).isoformat()
    out["updated_at"] = now

    con = dbcon()

    try:
        # Replace the legacy V1.7 table with the actual
        # dataframe produced by app.py V1.8.33.
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

def write_alerts(res):
    if res is None or res.empty:
        return

    top = res.head(
        int(CFG.get("max_opportunities", 10))
    ).copy()

    payload = {
        "generated_at":
            datetime.now(timezone.utc).isoformat(),
        "count": len(top),
        "opportunities": [],
    }

    for _, row in top.iterrows():

        item = {
            "ticker":
                str(row.get("ticker", "")),

            "company":
                str(row.get("company", "")),

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

        payload["opportunities"].append(item)

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
        datetime.now(timezone.utc).isoformat(),
    )


def safe_number(value):
    try:
        if pd.isna(value):
            return None

        return round(float(value), 6)

    except Exception:
        return None


# ============================================================
# MODEL
# ============================================================

def load_model():
    """
    Prefer the deployed V1.8 model.
    Fall back to the older JST loader only if needed.
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


def maybe_train(universe):
    hours = float(
        CFG.get(
            "jst_retrain_hours",
            24,
        )
    )

    if not stale(
        "jst_trained_at",
        hours,
    ):
        return "SKIP"

    log(
        "MODEL",
        "START",
        "scheduled model training",
    )

    try:
        model, rows, accuracy = (
            app.train_jst(universe)
        )

        if model is not None:
            log(
                "MODEL",
                "DONE",
                f"rows={rows} accuracy={accuracy}",
            )

            return "DONE"

        log(
            "MODEL",
            "SKIP",
            "insufficient training data",
        )

        return "SKIP"

    except Exception as exc:
        log(
            "MODEL",
            "ERROR",
            str(exc),
        )

        return "ERROR"


def maybe_walk_forward(universe):
    days = float(
        CFG.get(
            "walk_forward_days",
            7,
        )
    )

    if not stale(
        "walk_forward_at",
        days * 24,
    ):
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

        log(
            "WALK_FORWARD",
            "DONE",
            f"folds={len(out)} mean={mean}",
        )

        return "DONE"

    except Exception as exc:
        log(
            "WALK_FORWARD",
            "ERROR",
            str(exc),
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
        # Compatibility fallback in case model is optional
        # in the current app.py implementation.
        res = app.analyze_all(universe)

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

    # V1.8.23+ Dynamic Opportunity engine.
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
        # Dynamic failure must not destroy the main snapshot.
        log(
            "DYNAMIC",
            "ERROR",
            str(exc),
        )

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
# ENGINE CYCLE
# ============================================================

def cycle():
    ensure_tables()

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

    # --------------------------------------------------------
    # PHASE 1
    # FAST BOOTSTRAP
    # --------------------------------------------------------

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

    # First snapshot does NOT wait for training.
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

    # --------------------------------------------------------
    # PHASE 2
    # MODEL REFINE
    # --------------------------------------------------------

    maybe_train(bootstrap)

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

    # --------------------------------------------------------
    # PHASE 3
    # FULL UNIVERSE COLLECTION
    # --------------------------------------------------------

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

    # --------------------------------------------------------
    # PHASE 4
    # MODEL MAINTENANCE
    # --------------------------------------------------------

    maybe_train(universe)
    maybe_walk_forward(universe)

    model = load_model()

    # --------------------------------------------------------
    # PHASE 5
    # FULL V1.8 SNAPSHOT
    # --------------------------------------------------------

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

    log(
        "ENGINE",
        "DONE",
        f"results={len(result)}",
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
