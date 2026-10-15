from pathlib import Path

# Worker cleanup must use the authoritative full-universe source accepted by Top5.
p=Path('worker_base.py')
s=p.read_text(encoding='utf-8')
s=s.replace(
    'app.DB, pd.DataFrame(), source_phase="data-quality-block", config=CFG',
    'app.DB, pd.DataFrame(), source_phase="full-universe", config=CFG',
    1,
)
p.write_text(s,encoding='utf-8')

# H10 final write safety: stale drafts cannot carry an old BUY through a new block.
p=Path('premarket_scheduler.py')
s=p.read_text(encoding='utf-8')
s=s.replace(
    'sell = source_decision.isin({"SELL", "SELL/AVOID", "AVOID", "STRONG_SELL"})',
    'sell = source_decision.isin({"SELL", "SELL/AVOID", "AVOID", "STRONG_SELL", "EXIT"})',
    1,
)
helper='''def _sanitize_payload_for_quality(payload, now=None):
    """Re-check quality immediately before a preliminary/final database write."""
    quality_ok, quality_reason = _quality_allows_actionable(now)
    clean = dict(payload or {})
    if quality_ok:
        return clean
    picks = []
    for item in list(clean.get("picks") or []):
        row = dict(item or {})
        decision = str(row.get("decision") or "MONITOR").upper().strip()
        if decision in {"BUY", "STRONG_BUY", "NEAR ENTRY"}:
            row["decision"] = "WAIT"
        row["reason"] = (
            "MONITOR ONLY: data quality guard. "
            + str(quality_reason or "not ready")
            + ". "
            + str(row.get("reason") or "")
        )[:900]
        row["source_phase"] = f"premarket-h10:{VERSION}:QUALITY_MONITOR"
        picks.append(row)
    clean["picks"] = picks
    clean["mode"] = "QUALITY_MONITOR" if picks else "NO_TRADE"
    clean["reason"] = "Data quality guard: " + str(quality_reason or "not ready")
    return clean


'''
anchor='def select_candidates(res: pd.DataFrame):\n'
if helper not in s:
    if anchor not in s:
        raise SystemExit('quality sanitizer insert anchor missing')
    s=s.replace(anchor,helper+anchor,1)
old='''    picks = list(payload.get("picks") or [])
    mode = str(payload.get("mode") or "NO_TRADE")
'''
new='''    payload = _sanitize_payload_for_quality(payload, local)
    picks = list(payload.get("picks") or [])
    mode = str(payload.get("mode") or "NO_TRADE")
'''
if old in s:
    s=s.replace(old,new,1)
elif new not in s:
    raise SystemExit('finalize sanitizer anchor missing')
p.write_text(s,encoding='utf-8')

# Preliminary write gets the same last-moment safety check.
p=Path('premarket_preliminary.py')
s=p.read_text(encoding='utf-8')
old='''def _write_preliminary(payload, target_date):
    picks = list(payload.get("picks") or [])[: h10.MAX_PICKS]
'''
new='''def _write_preliminary(payload, target_date):
    payload = h10._sanitize_payload_for_quality(payload)
    picks = list(payload.get("picks") or [])[: h10.MAX_PICKS]
'''
if old in s:
    s=s.replace(old,new,1)
elif new not in s:
    raise SystemExit('preliminary sanitizer anchor missing')
p.write_text(s,encoding='utf-8')

print('R23 DATA-QUALITY FOLLOWUP APPLIED')
