from pathlib import Path

path=Path('recommendation_history.py')
src=path.read_text(encoding='utf-8')

if 'from market_session import session_status\n' not in src:
    src=src.replace('import pandas as pd\n','import pandas as pd\n\nfrom market_session import session_status\n',1)

# Add provenance/actionability columns to recommendation_history.
anchor='''    if "candidate_mode" not in existing:\n        con.execute("ALTER TABLE recommendation_history ADD COLUMN candidate_mode TEXT")\n'''
replacement='''    if "candidate_mode" not in existing:\n        con.execute("ALTER TABLE recommendation_history ADD COLUMN candidate_mode TEXT")\n    for col, typ in [("intraday_fresh", "INTEGER"), ("price_source", "TEXT"),\n                     ("session_status", "TEXT"), ("actionable_at_record", "INTEGER")]:\n        if col not in existing:\n            con.execute(f"ALTER TABLE recommendation_history ADD COLUMN {col} {typ}")\n'''
if anchor not in src:
    raise SystemExit('recommendation history migration anchor missing')
src=src.replace(anchor,replacement,1)

# Helper before record_snapshot.
insert_at=src.index('def record_snapshot(')
helper='''def _row_actionability(row, cfg=None, market=None):\n    """Paper-trade only actual executable entries during live market.\n\n    Off-market BUY/STRONG_BUY remains eligible for NEXT_SESSION shadow evaluation,\n    matching shadow_activation_rule=NEXT_SESSION. During a live regular session,\n    BUY must also be ENTRY READY + ACTIVE + fresh intraday evidence.\n    """\n    cfg = cfg or {}\n    market = dict(market or session_status(cfg) or {})\n    mode = _s(row.get("candidate_mode"), "STRICT").upper()\n    recommendation = _s(row.get("decision", row.get("state", "WATCH")), "WATCH").upper()\n    state = _s(row.get("state"), "").upper()\n    entry = _s(row.get("entry_status"), "").upper()\n    live = bool(market.get("live"))\n    fresh = bool(row.get("intraday_fresh"))\n    base = mode != "MONITOR_FALLBACK" and recommendation in {"BUY", "STRONG_BUY"}\n    if not base:\n        return False\n    if live:\n        return state == "ENTRY READY" and entry == "ACTIVE" and fresh\n    return True\n\n\n'''
src=src[:insert_at]+helper+src[insert_at:]

# Add market context once per snapshot.
market_anchor='''    market_regime = _s(dynamic_df.iloc[0].get("market_regime", "UNKNOWN"), "UNKNOWN")\n\n    con = _con(db_path)\n'''
market_repl='''    market_regime = _s(dynamic_df.iloc[0].get("market_regime", "UNKNOWN"), "UNKNOWN")\n    market = dict(session_status(cfg) or {})\n    market_status = _s(market.get("status"), "UNKNOWN").upper()\n\n    con = _con(db_path)\n'''
if market_anchor not in src:
    raise SystemExit('market context anchor missing')
src=src.replace(market_anchor,market_repl,1)

# Extend values/insert columns.
old_vals='''            vals = (\n                run_id, generated_at, mdate, ticker, _s(row.get("company")), rank,\n                recommendation, candidate_mode, _s(row.get("state")), _s(row.get("entry_status")),\n                _f(row.get("opportunity_score")), _f(row.get("ai_score")),\n                _f(row.get("p_up")), _f(row.get("p_down")), _f(row.get("confidence")),\n                _f(row.get("close")), _f(row.get("entry_low")), _f(row.get("entry_high")),\n                _f(row.get("stop_loss")), _f(row.get("target_1")), _f(row.get("target_2")),\n                _f(row.get("risk_reward")), _s(row.get("risk")), _s(row.get("market_regime")),\n                _s(row.get("reason")),\n            )\n'''
new_vals='''            actionable = _row_actionability(row, cfg=cfg, market=market)\n            vals = (\n                run_id, generated_at, mdate, ticker, _s(row.get("company")), rank,\n                recommendation, candidate_mode, _s(row.get("state")), _s(row.get("entry_status")),\n                _f(row.get("opportunity_score")), _f(row.get("ai_score")),\n                _f(row.get("p_up")), _f(row.get("p_down")), _f(row.get("confidence")),\n                _f(row.get("close")), _f(row.get("entry_low")), _f(row.get("entry_high")),\n                _f(row.get("stop_loss")), _f(row.get("target_1")), _f(row.get("target_2")),\n                _f(row.get("risk_reward")), _s(row.get("risk")), _s(row.get("market_regime")),\n                _s(row.get("reason")), 1 if bool(row.get("intraday_fresh")) else 0,\n                _s(row.get("price_source"), ""), market_status, 1 if actionable else 0,\n            )\n'''
if old_vals not in src:
    raise SystemExit('vals anchor missing')
src=src.replace(old_vals,new_vals,1)

old_insert='''                    reference_price,entry_low,entry_high,stop_loss,target_1,target_2,risk_reward,risk,\n                    market_regime,reason\n                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",\n'''
new_insert='''                    reference_price,entry_low,entry_high,stop_loss,target_1,target_2,risk_reward,risk,\n                    market_regime,reason,intraday_fresh,price_source,session_status,actionable_at_record\n                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",\n'''
if old_insert not in src:
    raise SystemExit('insert column anchor missing')
src=src.replace(old_insert,new_insert,1)

# Replace old weak actionability block with precomputed strict actionability.
old_action='''            actionable = (\n                candidate_mode != "MONITOR_FALLBACK"\n                and recommendation.upper() in {"BUY", "STRONG_BUY"}\n            )\n            if not actionable:\n                continue\n'''
new_action='''            if not actionable:\n                continue\n'''
if old_action not in src:
    raise SystemExit('weak actionability anchor missing')
src=src.replace(old_action,new_action,1)

path.write_text(src,encoding='utf-8')
print('R33 EVALUATION INTEGRITY PATCH APPLIED')
