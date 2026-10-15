from pathlib import Path

# ---------------- Worker: DATA_QUALITY block invalidates every actionable live surface ----------------
p=Path('worker_base.py')
s=p.read_text(encoding='utf-8')
old='''        if not quality["ready"]:\n            log("DATA_QUALITY", "BLOCK", " | ".join(quality["reasons"]))\n            return pd.DataFrame()\n'''
new='''        if not quality["ready"]:\n            reason_text=" | ".join(quality["reasons"])\n            log("DATA_QUALITY", "BLOCK", reason_text)\n            # A blocked full-universe cycle is authoritative evidence that old\n            # actionable state must not survive as if it were current. Keep the\n            # raw opportunity snapshot for diagnostics/monitor-only recovery,\n            # but clear all live/actionable derivatives and pending alerts.\n            try:\n                dynamic_engine._clear_dynamic_current("DATA_QUALITY_BLOCK")\n            except Exception:\n                log("DATA_QUALITY", "DYNAMIC_CLEAR_ERROR", traceback.format_exc()[-900:])\n            try:\n                hold_engine._clear_current("DATA_QUALITY_BLOCK")\n            except Exception:\n                log("DATA_QUALITY", "HOLD_CLEAR_ERROR", traceback.format_exc()[-900:])\n            try:\n                cleared=top5_timeline.record_top5(\n                    app.DB, pd.DataFrame(), source_phase="data-quality-block", config=CFG\n                )\n                log("DATA_QUALITY", "TOP5_CLEARED", str(cleared))\n            except Exception:\n                log("DATA_QUALITY", "TOP5_CLEAR_ERROR", traceback.format_exc()[-900:])\n            app.state_set("actionability_status", "DATA_QUALITY_BLOCK")\n            app.state_set("actionability_reason", reason_text)\n            return pd.DataFrame()\n'''
if old in s:
    s=s.replace(old,new,1)
elif new not in s:
    raise SystemExit('worker data-quality block anchor missing')
p.write_text(s,encoding='utf-8')

# ---------------- H10: BUY is allowed only from a fresh READY quality state ----------------
p=Path('premarket_scheduler.py')
s=p.read_text(encoding='utf-8')
helper='''def _quality_allows_actionable(now=None):\n    """Return whether Pre-Market may emit actionable BUY decisions.\n\n    Unknown, blocked or stale quality never removes monitor candidates; it only\n    downgrades the surface to monitor-only so H-10 cannot bypass worker safety.\n    """\n    status=str(_state_get("data_quality_status", "") or "").upper().strip()\n    reasons=str(_state_get("data_quality_reasons", "") or "").strip()\n    if status != "READY":\n        return False, reasons or f"data quality status {status or 'UNKNOWN'}"\n    raw=str(_state_get("last_collection", "") or "").strip()\n    if not raw:\n        return False, "missing collection timestamp"\n    try:\n        dt=datetime.fromisoformat(raw.replace("Z", "+00:00"))\n        if dt.tzinfo is None:\n            dt=dt.replace(tzinfo=timezone.utc)\n        current=_now_wib(now).astimezone(timezone.utc)\n        age_hours=max(0.0,(current-dt.astimezone(timezone.utc)).total_seconds()/3600.0)\n    except Exception:\n        return False, "invalid collection timestamp"\n    max_age=max(0.5,float(CFG.get("data_quality_max_age_hours",8)))\n    if age_hours > max_age:\n        return False, f"collection age {age_hours:.1f}h > {max_age:.1f}h"\n    return True, "READY"\n\n\n'''
anchor='def select_candidates(res: pd.DataFrame):\n'
if helper not in s:
    if anchor not in s:
        raise SystemExit('scheduler select anchor missing')
    s=s.replace(anchor,helper+anchor,1)
old='''def select_candidates(res: pd.DataFrame):\n    """Strict BUY first; otherwise honest best-available monitor candidates."""\n    strict = premarket_intelligence.select_high_conviction(res, CFG)\n'''
new='''def select_candidates(res: pd.DataFrame):\n    """Strict BUY only when runtime data quality is fresh and READY."""\n    quality_ok, quality_reason = _quality_allows_actionable()\n    if not quality_ok:\n        monitor = _select_monitor_candidates(res)\n        if not monitor.empty:\n            return (\n                monitor,\n                "QUALITY_MONITOR",\n                "Data quality guard: " + str(quality_reason or "not ready"),\n            )\n        return pd.DataFrame(), "NO_TRADE", "Data quality guard: " + str(quality_reason or "not ready")\n    strict = premarket_intelligence.select_high_conviction(res, CFG)\n'''
if old in s:
    s=s.replace(old,new,1)
elif new not in s:
    raise SystemExit('scheduler quality select anchor missing')
p.write_text(s,encoding='utf-8')

print('R23 DATA-QUALITY SAFETY PATCH APPLIED')
