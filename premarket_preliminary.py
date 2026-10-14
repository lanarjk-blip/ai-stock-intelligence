"""Always-visible preliminary Pre-Market candidates.

This daemon complements the hard H-10 scheduler. It keeps the Pre-Market
surface useful before the 08:50 WIB final lock without pretending early
candidates are final recommendations.

Rules:
- Before 08:50 on a trading day, publish up to five PRELIMINARY candidates.
- After market close, publish PRELIMINARY candidates for the next trading day.
- On weekends/holidays, keep next-session PRELIMINARY candidates visible.
- During the live market, preserve today's H-10 locked snapshot.
- Never overwrite an H10_FINAL run.
- Candidate selection reuses premarket_scheduler.select_candidates(), so failed
  strict BUY rows remain WAIT/MONITOR instead of being fabricated into BUY.
"""
from __future__ import annotations

import sqlite3
import time
import traceback
from datetime import datetime, timezone

import premarket_scheduler as h10
import market_session

CFG = h10.CFG
VERSION = "PRELIM-R1"


def _state_get(key, default=""):
    try:
        value = h10.app.state_get(key)
        return default if value is None else value
    except Exception:
        return default


def _state_set(key, value):
    try:
        h10.app.state_set(key, value)
    except Exception:
        pass


def _due(seconds: int) -> bool:
    raw = str(_state_get("premarket_preliminary_at", "") or "")
    if not raw:
        return True
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        age = (datetime.now(timezone.utc) - dt.astimezone(timezone.utc)).total_seconds()
        return age >= int(seconds)
    except Exception:
        return True


def _publish_window(local) -> tuple[bool, int]:
    """Return (should_publish, refresh_seconds)."""
    state = h10.window_state(local)
    if state == "BEFORE":
        return True, 30 * 60
    if state == "PREPARE":
        return True, 5 * 60
    if state == "LIGHT":
        return True, 60
    if state == "OFF":
        return True, 30 * 60
    if state == "AFTER_OPEN":
        session = market_session.session_status(CFG, now=local)
        if str(session.get("status") or "").upper() == "CLOSED":
            return True, 30 * 60
    return False, 60


def _payload_for(target_date: str, local):
    # Prefer the H-10 draft when one already exists and targets the same session.
    payload = h10._load_draft(target_date)
    if payload and payload.get("picks"):
        return payload

    snapshot = h10._read_latest_snapshot()
    if snapshot is None or snapshot.empty:
        payload = h10.build_draft(local, lightweight=True)
        return payload if payload and payload.get("picks") else None

    picks, mode, reason = h10.select_candidates(snapshot)
    payload = h10._save_draft(snapshot, target_date, mode, reason)
    return payload if payload and payload.get("picks") else None


def publish_preliminary(now=None):
    local = h10._now_wib(now)
    target_date = h10._target_market_date(local)

    # Final H-10 output is immutable. Preliminary must never touch it.
    if h10._already_finalized(target_date):
        return {"status": "FINAL_EXISTS", "market_date": target_date}

    payload = _payload_for(target_date, local)
    if not payload:
        return {"status": "NO_CANDIDATE", "market_date": target_date}

    picks = list(payload.get("picks") or [])[: h10.MAX_PICKS]
    if not picks:
        return {"status": "NO_CANDIDATE", "market_date": target_date}

    generated_at = datetime.now(timezone.utc).isoformat()
    mode = str(payload.get("mode") or "MONITOR_FALLBACK")
    note = (
        f"PRELIMINARY {VERSION} mode={mode} target={target_date} "
        f"source_draft={payload.get('generated_at','')} final_lock={h10.FINAL_LOCK} WIB"
    )[:1800]

    h10._ensure_tables()
    con = sqlite3.connect(h10.app.DB, timeout=30)
    con.execute("PRAGMA busy_timeout=30000")
    try:
        con.execute("BEGIN IMMEDIATE")
        current = con.execute(
            "SELECT note FROM premarket_runs WHERE market_date=?",
            (target_date,),
        ).fetchone()
        if current and str(current[0] or "").startswith("H10_FINAL"):
            con.rollback()
            return {"status": "FINAL_EXISTS", "market_date": target_date}

        con.execute("DELETE FROM premarket_picks WHERE market_date=?", (target_date,))
        con.execute("DELETE FROM premarket_runs WHERE market_date=?", (target_date,))

        for rank, item in enumerate(picks, start=1):
            con.execute(
                """INSERT INTO premarket_picks
                (market_date,generated_at,rank,ticker,company,decision,confidence,
                 ai_score,risk,entry_low,entry_high,stop_loss,target_1,target_2,
                 ihsg_regime,reason,source_phase)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    target_date,
                    generated_at,
                    rank,
                    str(item.get("ticker") or ""),
                    str(item.get("company") or ""),
                    str(item.get("decision") or "MONITOR"),
                    h10._number(item.get("confidence")),
                    h10._number(item.get("ai_score")),
                    str(item.get("risk") or ""),
                    h10._number(item.get("entry_low")),
                    h10._number(item.get("entry_high")),
                    h10._number(item.get("stop_loss")),
                    h10._number(item.get("target_1")),
                    h10._number(item.get("target_2")),
                    str(item.get("ihsg_regime") or ""),
                    "PRELIMINARY ONLY • belum final H-10. " + str(item.get("reason") or ""),
                    f"premarket-preliminary:{VERSION}:{mode}",
                ),
            )

        con.execute(
            """INSERT INTO premarket_runs
            (market_date,generated_at,status,pick_count,note)
            VALUES(?,?,?,?,?)""",
            (target_date, generated_at, "PRELIMINARY", len(picks), note),
        )
        con.commit()
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()

    _state_set("premarket_status", "PRELIMINARY")
    _state_set("premarket_market_date", target_date)
    _state_set("premarket_pick_count", len(picks))
    _state_set("premarket_preliminary_at", generated_at)
    _state_set("premarket_preliminary_market_date", target_date)
    _state_set("premarket_preliminary_mode", mode)
    h10._log("PRELIMINARY", f"market_date={target_date} mode={mode} picks={len(picks)}")
    return {
        "status": "PRELIMINARY",
        "market_date": target_date,
        "mode": mode,
        "pick_count": len(picks),
    }


def run_forever():
    h10._log("PRELIMINARY_START", f"version={VERSION} final_lock={h10.FINAL_LOCK}")
    while True:
        try:
            local = h10._now_wib()
            allowed, refresh_seconds = _publish_window(local)
            target_date = h10._target_market_date(local)
            if allowed and not h10._already_finalized(target_date) and _due(refresh_seconds):
                publish_preliminary(local)
            time.sleep(15 if allowed else 60)
        except Exception:
            h10._log("PRELIMINARY_ERROR", traceback.format_exc()[-1800:])
            time.sleep(15)


if __name__ == "__main__":
    run_forever()
