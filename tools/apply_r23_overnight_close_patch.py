from pathlib import Path

p=Path('worker_base.py')
s=p.read_text(encoding='utf-8')

helper='''def _refresh_closing_overnight(res, now=None):
    """Publish next-session Overnight Intelligence only after the IDX close.

    Live, break and pre-market cycles preserve the last immutable closing snapshot.
    """
    session = market_session.session_status(CFG, now=now)
    status = str(session.get("status") or "").upper()
    market_date = str(session.get("market_date") or "")
    if status != "CLOSED":
        app.state_set("overnight_refresh_status", f"PRESERVED_{status or 'UNKNOWN'}")
        log("OVERNIGHT", "PRESERVED", f"session={status or 'UNKNOWN'} source_market_date={market_date}")
        return {
            "status": "PRESERVED",
            "session": status,
            "market_date": market_date,
            "pick_count": int(app.state_get("overnight_pick_count") or 0),
        }

    overnight = closing_overnight.select_overnight(res, CFG)
    picks = overnight.get("picks")
    target_date = market_session.next_trading_date(CFG, now=now, after_close=True)
    generated_at = datetime.now(timezone.utc).isoformat()
    app.state_set("overnight_status", overnight.get("status", ""))
    app.state_set("overnight_pick_count", 0 if picks is None else len(picks))
    app.state_set("overnight_reason", overnight.get("reason", ""))
    app.state_set("overnight_generated_at", generated_at)
    app.state_set("overnight_source_market_date", market_date)
    app.state_set("overnight_target_market_date", target_date)
    app.state_set("overnight_refresh_status", "CLOSING_FINAL")
    if picks is not None and not picks.empty:
        payload = picks.to_dict(orient="records")
        app.state_set("overnight_picks_json", json.dumps(payload, ensure_ascii=False, default=str))
    else:
        app.state_set("overnight_picks_json", "[]")
    count = 0 if picks is None else len(picks)
    log(
        "OVERNIGHT",
        overnight.get("status", "UNKNOWN"),
        f"picks={count} source_market_date={market_date} target_market_date={target_date}",
    )
    return {
        "status": overnight.get("status", ""),
        "session": status,
        "market_date": market_date,
        "target_market_date": target_date,
        "pick_count": count,
    }


'''
anchor='# ============================================================\n# ENGINE CYCLE\n# ============================================================\n'
if helper not in s:
    if anchor not in s:
        raise SystemExit('engine cycle anchor missing')
    s=s.replace(anchor,helper+anchor,1)

old='''    # V1.8.44: strict next-session Closing / Overnight Intelligence.
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
'''
new='''    # Strict next-session Closing / Overnight Intelligence is immutable during
    # the live session. Only a completed regular session may publish/replace it.
    if str(source).lower() == "full-universe":
        try:
            _refresh_closing_overnight(res)
        except Exception:
            app.state_set("overnight_refresh_status", "ERROR")
            app.state_set("overnight_error", traceback.format_exc()[-900:])
            log("OVERNIGHT", "ERROR", traceback.format_exc()[-1800:])
'''
if old in s:
    s=s.replace(old,new,1)
elif new not in s:
    raise SystemExit('overnight publication block missing')

p.write_text(s,encoding='utf-8')
print('R23 CLOSE-ONLY OVERNIGHT PATCH APPLIED')
