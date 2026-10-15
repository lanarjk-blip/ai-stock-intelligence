from pathlib import Path

# worker_base: one writer owns Pre-Market; wire Hold Intelligence into full-universe cycles.
p=Path('worker_base.py')
s=p.read_text(encoding='utf-8')

if 'import hold_engine\n' not in s:
    anchor='import closing_overnight\nimport market_session\n'
    if anchor not in s:
        raise SystemExit('worker import anchor missing')
    s=s.replace(anchor,'import closing_overnight\nimport hold_engine\nimport market_session\n',1)

old='''        # V1.8.41 Stage 2: Pre-Market is separate from live Dynamic Opportunity.\n        try:\n            pre = lock_premarket_picks(res, source_phase=source)\n            app.state_set("premarket_status", pre.get("status", ""))\n        except Exception:\n            log("PREMARKET", "ERROR", traceback.format_exc()[-1800:])\n'''
new='''        # PRELIM-R2/H10-R1 are the sole Pre-Market writers. The legacy full-universe\n        # lock used to race the 08:50 scheduler and could expose an early LOCKED row.\n        log("PREMARKET", "SCHEDULER_OWNED", "legacy full-universe lock disabled; PRELIM-R2/H10-R1 own writes")\n'''
if old in s:
    s=s.replace(old,new,1)
elif new not in s:
    raise SystemExit('legacy premarket call anchor missing')

# Add Hold Intelligence before strict overnight selection, using actual market-session phase.
hold_block='''    # V1.8.44: strict next-session Closing / Overnight Intelligence.\n    if str(source).lower() == "full-universe":\n'''
hold_new='''    # Hold Horizon intelligence is refreshed only from the authoritative full-universe\n    # result and receives the actual IDX session phase, not the scan-source label.\n    if str(source).lower() == "full-universe":\n        try:\n            session = market_session.session_status(CFG)\n            hold_phase = str(session.get("status") or "CLOSED").upper()\n            hold = hold_engine.update(res, phase=hold_phase)\n            app.state_set("hold_intelligence_status", "READY" if hold is not None and not hold.empty else "NO_CANDIDATE")\n            log("HOLD", "UPDATED" if hold is not None and not hold.empty else "NO_CANDIDATE",\n                f"phase={hold_phase} candidates={0 if hold is None else len(hold)}")\n        except Exception:\n            app.state_set("hold_intelligence_status", "ERROR")\n            app.state_set("hold_intelligence_error", traceback.format_exc()[-900:])\n            log("HOLD", "ERROR", traceback.format_exc()[-1800:])\n\n    # V1.8.44: strict next-session Closing / Overnight Intelligence.\n    if str(source).lower() == "full-universe":\n'''
if hold_block in s:
    s=s.replace(hold_block,hold_new,1)
elif hold_new not in s:
    raise SystemExit('hold wiring anchor missing')

p.write_text(s,encoding='utf-8')

# hold_engine: normalize sell aliases and decision/risk strings.
p=Path('hold_engine.py')
h=p.read_text(encoding='utf-8')
h=h.replace("    risk=str(row.risk)\n    risk_penalty={'LOW':0.0,'MEDIUM':3.0,'HIGH':7.0}.get(risk,5.0)\n","    risk=str(row.risk or '').upper().strip()\n    risk_penalty={'LOW':0.0,'MEDIUM':3.0,'HIGH':7.0,'EXTREME':12.0}.get(risk,6.0)\n",1) if "risk_penalty={'LOW':0.0" in h else h
# Actual hold_engine uses risk_pen variable, patch exact current block.
h=h.replace("    risk_pen={'LOW':0,'MEDIUM':4,'HIGH':9}.get(str(r.risk),6)\n","    risk_pen={'LOW':0,'MEDIUM':4,'HIGH':9,'EXTREME':14}.get(str(r.risk or '').upper().strip(),6)\n",1)
h=h.replace("    edge=float(ts['edge']); risk=str(r.risk)\n","    edge=float(ts['edge']); risk=str(r.risk or '').upper().strip()\n",1)
h=h.replace("    dec=str(r.decision)\n","    dec=str(r.decision or '').upper().strip()\n",1)
h=h.replace("    if score>=70 and dec in ('BUY','NEAR ENTRY','WATCH'): return 'HOLD CANDIDATE'\n","    if score>=70 and dec in ('BUY','STRONG_BUY','NEAR ENTRY','WATCH'): return 'HOLD CANDIDATE'\n",1)
old_filter="    c=res.copy(); c=c[~c.decision.isin(['SELL/AVOID'])].copy()\n"
new_filter="    c=res.copy(); decisions=c.decision.astype(str).str.upper().str.strip(); c=c[~decisions.isin({'SELL','SELL/AVOID','AVOID','STRONG_SELL','EXIT'})].copy()\n"
if old_filter in h:
    h=h.replace(old_filter,new_filter,1)
elif new_filter not in h:
    raise SystemExit('hold sell filter anchor missing')
p.write_text(h,encoding='utf-8')

print('R23 WORKER OWNERSHIP + HOLD WIRING PATCH APPLIED')
