"""Always-visible preliminary Pre-Market candidates.

This daemon complements the hard H-10 scheduler. It keeps the Pre-Market
surface useful before the 08:50 WIB final lock without pretending early
candidates are final recommendations.

R77 policy:
- Pre-Market recommendation slots are quality-only. WAIT/WATCH/MONITOR rows may
  remain visible elsewhere in the app, but they must not be promoted into the
  ranked Pre-Market Top list merely to fill five cards.
- If no strict actionable candidate passes the gates, publish an explicit
  zero-pick NO_TRADE run and clear any older provisional picks for that date.
- The same policy is installed onto premarket_scheduler.select_candidates(), so
  the 08:50 H-10 finalizer follows the identical no-forced-picks rule.
"""
from __future__ import annotations

import sqlite3
import time
import traceback
from datetime import datetime, timezone

import pandas as pd
import premarket_scheduler as h10
import market_session

CFG = h10.CFG
VERSION = "PRELIM-R4-NO-FORCED-TOP5"


def _install_no_forced_top5_policy():
    current = h10.select_candidates
    if getattr(current, "_r77_no_forced_top5", False):
        return

    original = current

    def strict_select_candidates(res):
        picks, mode, reason = original(res)
        mode_text = str(mode or "").upper().strip()
        if mode_text in {"MONITOR_FALLBACK", "QUALITY_MONITOR"}:
            detail = str(reason or "No strict actionable candidate passed.")
            return (
                pd.DataFrame(),
                "NO_TRADE",
                "R77 quality-only policy: no strict actionable candidate passed; "
                "WAIT/WATCH/MONITOR rows are not allowed to fill the ranked Pre-Market list. "
                + detail,
            )
        return picks, mode, reason

    strict_select_candidates._r77_no_forced_top5 = True
    strict_select_candidates._r77_original = original
    h10.select_candidates = strict_select_candidates


_install_no_forced_top5_policy()


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


def _payload_for(target_date: str, local, allow_full_analysis=True):
    # A zero-pick NO_TRADE draft is valid state and must not be discarded,
    # otherwise an older provisional list can survive and look current.
    payload = h10._load_draft(target_date)
    if payload:
        return payload

    snapshot = h10._read_latest_snapshot()
    if snapshot is None or snapshot.empty:
        if not allow_full_analysis:
            return None
        return h10.build_draft(local, lightweight=True)

    picks, mode, reason = h10.select_candidates(snapshot)
    return h10._save_draft(snapshot, target_date, mode, reason)


def _write_preliminary(payload, target_date):
    payload = h10._sanitize_payload_for_quality(payload)
    # Quality sanitizer may turn an otherwise actionable payload into monitor
    # only. Apply R77 one final time immediately before persistence.
    mode = str(payload.get("mode") or "NO_TRADE").upper().strip()
    raw_picks = list(payload.get("picks") or [])[: h10.MAX_PICKS]
    if mode in {"MONITOR_FALLBACK", "QUALITY_MONITOR"}:
        raw_picks = []
        mode = "NO_TRADE"
        payload["reason"] = (
            "R77 quality-only policy: monitor candidates are not ranked recommendations. "
            + str(payload.get("reason") or "")
        )[:1200]
    picks = raw_picks

    generated_at = datetime.now(timezone.utc).isoformat()
    note = (
        f"PRELIMINARY {VERSION} mode={mode} target={target_date} "
        f"source_draft={payload.get('generated_at','')} final_lock={h10.FINAL_LOCK} WIB "
        f"reason={payload.get('reason','')}"
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

        # Exact replacement semantics. Zero picks intentionally clears an older
        # preliminary Top list instead of leaving zombie recommendations behind.
        con.execute("DELETE FROM premarket_picks WHERE market_date=?", (target_date,))
        con.execute("DELETE FROM premarket_runs WHERE market_date=?", (target_date,))

        for rank, item in enumerate(picks, start=1):
            con.execute(
                """INSERT INTO premarket_picks
                (market_date,generated_at,rank,ticker,company,decision,confidence,
                 ai_score,risk,reference_price,entry_low,entry_high,stop_loss,target_1,target_2,
                 ihsg_regime,reason,source_phase)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
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
                    h10._number(item.get("reference_price")),
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

        status = "PRELIMINARY" if picks else "NO_TRADE"
        con.execute(
            """INSERT INTO premarket_runs
            (market_date,generated_at,status,pick_count,note)
            VALUES(?,?,?,?,?)""",
            (target_date, generated_at, status, len(picks), note),
        )
        con.commit()
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()

    _state_set("premarket_status", status)
    _state_set("premarket_market_date", target_date)
    _state_set("premarket_pick_count", len(picks))
    _state_set("premarket_preliminary_at", generated_at)
    _state_set("premarket_preliminary_market_date", target_date)
    _state_set("premarket_preliminary_mode", mode)
    h10._log("PRELIMINARY", f"market_date={target_date} status={status} mode={mode} picks={len(picks)}")
    return {
        "status": status,
        "market_date": target_date,
        "mode": mode,
        "pick_count": len(picks),
    }


def publish_preliminary(now=None):
    local = h10._now_wib(now)
    target_date = h10._target_market_date(local)

    if h10._already_finalized(target_date):
        return {"status": "FINAL_EXISTS", "market_date": target_date}

    payload = _payload_for(target_date, local, allow_full_analysis=True)
    if not payload:
        return {"status": "NO_CANDIDATE", "market_date": target_date}
    return _write_preliminary(payload, target_date)


def ensure_on_open(now=None):
    """Self-heal Pre-Market without permitting post-open hindsight edits."""
    local = h10._now_wib(now)
    target_date = h10._target_market_date(local)
    window = h10.window_state(local)

    if h10._already_finalized(target_date):
        return {"status": "FINAL_EXISTS", "market_date": target_date}

    if window == "FINALIZE":
        result = h10.finalize_h10(local)
        result["self_healed"] = True
        return result

    if window == "AFTER_OPEN":
        session = market_session.session_status(CFG, now=local)
        if str(session.get("status") or "").upper() != "CLOSED":
            return {
                "status": "MISSED_H10",
                "market_date": str(session.get("market_date") or target_date),
                "detail": "H-10 final tidak tersedia sebelum market open; BUY pre-market diblokir.",
            }

    allowed, refresh_seconds = _publish_window(local)
    if not allowed:
        return {"status": "WINDOW_BLOCKED", "market_date": target_date}
    if not _due(refresh_seconds):
        return {"status": "FRESH", "market_date": target_date}

    snapshot = h10._read_latest_snapshot()
    if snapshot is None or snapshot.empty:
        return {"status": "NO_SNAPSHOT", "market_date": target_date}

    payload = _payload_for(target_date, local, allow_full_analysis=False)
    if not payload:
        return {"status": "NO_CANDIDATE", "market_date": target_date}
    result = _write_preliminary(payload, target_date)
    h10._log(
        "PRELIMINARY_ON_OPEN",
        f"market_date={target_date} status={result.get('status')} picks={result.get('pick_count',0)}",
    )
    return result


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
