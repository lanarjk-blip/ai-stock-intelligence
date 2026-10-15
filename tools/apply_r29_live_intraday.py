from pathlib import Path

# R29: live intraday freshness for background worker + Dynamic Opportunity.

worker_path = Path('worker_base.py')
worker = worker_path.read_text(encoding='utf-8')

anchor = '\ndef full_collection_status(universe_count):\n'
if anchor not in worker:
    raise SystemExit('worker full_collection_status anchor missing')

intraday_worker = r'''

def _state_age_seconds(key, now=None):
    value = app.state_get(key)
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        current = now or datetime.now(timezone.utc)
        if current.tzinfo is None:
            current = current.replace(tzinfo=timezone.utc)
        return max(0.0, (current.astimezone(timezone.utc) - dt.astimezone(timezone.utc)).total_seconds())
    except Exception:
        return None


def intraday_refresh_due(now=None):
    """Refresh live candidate bars on a bounded cadence, never off-market."""
    session = market_session.session_status(CFG, now=now)
    status = str(session.get("status") or "").upper()
    market_date = str(session.get("market_date") or "")
    if not bool(session.get("live")):
        return False, f"NOT_LIVE_{status or 'UNKNOWN'}"

    if str(app.state_get("last_intraday_market_date") or "") != market_date:
        return True, "NEW_MARKET_DATE"

    current = now
    if current is None:
        current = datetime.now(timezone.utc)
    elif current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)

    refresh_seconds = max(5, int(CFG.get("intraday_refresh_minutes", 30))) * 60
    success_age = _state_age_seconds("last_intraday_collection", current)
    if success_age is not None and success_age < refresh_seconds:
        return False, f"FRESH_{success_age:.0f}s"

    retry_seconds = max(5, int(CFG.get("intraday_refresh_retry_minutes", 10))) * 60
    attempt_age = _state_age_seconds("last_intraday_attempt", current)
    if success_age is None and attempt_age is not None and attempt_age < retry_seconds:
        return False, f"RETRY_BACKOFF_{attempt_age:.0f}s"
    return True, "DUE"


def _intraday_candidate_tickers(res, now=None):
    """Prioritize H-10 + current Dynamic + strongest daily candidates."""
    limit = max(5, int(CFG.get("intraday_symbols_per_cycle", 60)))
    ordered = []

    def add(value):
        ticker = str(value or "").upper().replace(".JK", "").strip()
        if ticker and ticker not in ordered:
            ordered.append(ticker)

    try:
        con = dbcon()
        if con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='dynamic_opportunities'").fetchone():
            for row in con.execute("SELECT ticker FROM dynamic_opportunities ORDER BY rank LIMIT 20").fetchall():
                add(row[0])
        session = market_session.session_status(CFG, now=now)
        market_date = str(session.get("market_date") or "")
        run = None
        if con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='premarket_runs'").fetchone():
            run = con.execute("SELECT status,note FROM premarket_runs WHERE market_date=?", (market_date,)).fetchone()
        if run and str(run[0] or "").upper() == "LOCKED" and str(run[1] or "").startswith("H10_FINAL"):
            for row in con.execute("SELECT ticker FROM premarket_picks WHERE market_date=? ORDER BY rank", (market_date,)).fetchall():
                add(row[0])
        con.close()
    except Exception:
        try:
            con.close()
        except Exception:
            pass

    if res is not None and not res.empty:
        ranked = res.copy()
        sort_cols = [c for c in ("relative_rank_score", "ai_score") if c in ranked.columns]
        if sort_cols:
            ranked = ranked.sort_values(sort_cols, ascending=[False] * len(sort_cols))
        for ticker in ranked.get("ticker", pd.Series(dtype=str)).tolist():
            add(ticker)
            if len(ordered) >= limit:
                break
    return ordered[:limit]


def _intraday_history_count(ticker):
    try:
        con = dbcon()
        row = con.execute("SELECT COUNT(*) FROM intraday WHERE ticker=?", (str(ticker),)).fetchone()
        con.close()
        return int(row[0] if row else 0)
    except Exception:
        return 0


def _fetch_intraday_candidate(ticker, company=""):
    try:
        full_days = max(5, int(CFG.get("intraday_period_days", 60)))
        refresh_days = max(2, int(CFG.get("intraday_refresh_period_days", 5)))
        period_days = full_days if _intraday_history_count(ticker) < 60 else min(full_days, refresh_days)
        recover = getattr(app, "fetch_chart_recover", None)
        if callable(recover):
            value = recover(
                ticker,
                period_days,
                CFG.get("intraday_interval", "30m"),
                company=company,
            )
            df = _recover_dataframe(value)
        else:
            df = app.fetch_chart(ticker, period_days, CFG.get("intraday_interval", "30m"))
        return str(ticker), df, ""
    except Exception as exc:
        return str(ticker), pd.DataFrame(), str(exc)[:300]


def refresh_intraday_candidates(res, now=None, force=False):
    """Refresh only relevant live candidates and persist bars serially."""
    session = market_session.session_status(CFG, now=now)
    if not bool(session.get("live")):
        status = str(session.get("status") or "UNKNOWN").upper()
        app.state_set("intraday_refresh_status", f"SKIP_{status}")
        return {"status": f"SKIP_{status}", "ok": 0, "rows": 0, "errors": 0, "tickers": 0}

    due, reason = intraday_refresh_due(now=now)
    if not force and not due:
        app.state_set("intraday_refresh_status", f"SKIP_{reason}")
        return {"status": f"SKIP_{reason}", "ok": 0, "rows": 0, "errors": 0, "tickers": 0}

    tickers = _intraday_candidate_tickers(res, now=now)
    if not tickers:
        app.state_set("intraday_refresh_status", "SKIP_NO_CANDIDATES")
        return {"status": "SKIP_NO_CANDIDATES", "ok": 0, "rows": 0, "errors": 0, "tickers": 0}

    company_map = {}
    if res is not None and not res.empty and "ticker" in res.columns:
        for _, row in res.iterrows():
            company_map[str(row.get("ticker") or "").upper().replace(".JK", "")] = str(row.get("company") or "")

    attempt_at = datetime.now(timezone.utc).isoformat()
    app.state_set("last_intraday_attempt", attempt_at)
    mark_engine_progress("INTRADAY_REFRESH_START", f"tickers={len(tickers)} reason={reason}")
    log("INTRADAY", "START", f"tickers={len(tickers)} reason={reason}")

    workers = max(1, min(4, int(CFG.get("collector_workers", 6)), len(tickers)))
    ok = 0
    rows = 0
    errors = []
    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {
            executor.submit(_fetch_intraday_candidate, ticker, company_map.get(ticker, "")): ticker
            for ticker in tickers
        }
        for future in as_completed(futures):
            ticker = futures[future]
            try:
                _, df, err = future.result()
                if err:
                    errors.append(f"{ticker}: {err}")
                    continue
                if df is None or df.empty:
                    errors.append(f"{ticker}: empty intraday result")
                    continue
                saved = int(app.save_intraday(df, ticker) or 0)
                rows += saved
                ok += 1
            except Exception as exc:
                errors.append(f"{ticker}: {str(exc)[:300]}")

    market_date = str(session.get("market_date") or "")
    app.state_set("last_intraday_market_date", market_date)
    app.state_set("last_intraday_ok", ok)
    app.state_set("last_intraday_rows", rows)
    app.state_set("last_intraday_errors", len(errors))
    app.state_set("last_intraday_tickers", len(tickers))
    if ok > 0:
        completed_at = datetime.now(timezone.utc).isoformat()
        app.state_set("last_intraday_collection", completed_at)
        status = "READY" if ok == len(tickers) else "PARTIAL"
    else:
        status = "ERROR"
    app.state_set("intraday_refresh_status", status)
    if errors:
        app.state_set("intraday_refresh_error_sample", " | ".join(errors[:5])[:1500])
    mark_engine_progress("INTRADAY_REFRESH_DONE", f"ok={ok}/{len(tickers)} rows={rows} errors={len(errors)}")
    log("INTRADAY", status, f"ok={ok}/{len(tickers)} rows={rows} errors={len(errors)}")
    return {"status": status, "ok": ok, "rows": rows, "errors": len(errors), "tickers": len(tickers)}
'''
worker = worker.replace(anchor, intraday_worker + anchor, 1)

publish_anchor = '''    saved = save_opportunities(\n        res,\n        source,\n    )\n'''
if publish_anchor not in worker:
    raise SystemExit('worker publish anchor missing')
worker_intraday_call = '''    if str(source).lower() == "full-universe":\n        try:\n            intraday_result = refresh_intraday_candidates(res)\n            log(\n                "INTRADAY",\n                "CYCLE",\n                f"status={intraday_result.get('status')} ok={intraday_result.get('ok',0)}/{intraday_result.get('tickers',0)} rows={intraday_result.get('rows',0)}",\n            )\n        except Exception:\n            app.state_set("intraday_refresh_status", "ERROR")\n            app.state_set("intraday_refresh_error", traceback.format_exc()[-900:])\n            log("INTRADAY", "ERROR", traceback.format_exc()[-1800:])\n\n'''
worker = worker.replace(publish_anchor, worker_intraday_call + publish_anchor, 1)
worker_path.write_text(worker, encoding='utf-8')

# Dynamic engine: refuse live ENTRY READY on stale bars and overlay same-day live price.
dyn_path = Path('dynamic_engine.py')
dyn = dyn_path.read_text(encoding='utf-8')
start = dyn.index('def _cached_time_signal(ticker):')
end = dyn.index('\ndef _market_context(row):', start)
new_time = r'''def _latest_intraday_live_price(ticker, now=None):
    """Return only a same-market-date, sufficiently fresh live bar."""
    session = market_session.session_status(CFG, now=now)
    live = bool(session.get("live"))
    market_date = str(session.get("market_date") or "")
    result = {
        "live_required": live,
        "fresh": False,
        "price": None,
        "ts": "",
        "age_minutes": None,
        "market_date": market_date,
    }
    if not live:
        return result
    try:
        con = dbcon()
        d = pd.read_sql_query(
            "SELECT ts,close FROM intraday WHERE ticker=? ORDER BY ts DESC LIMIT 20",
            con,
            params=(str(ticker).upper().replace('.JK',''),),
        )
        con.close()
        if d.empty:
            return result
        d['ts'] = pd.to_datetime(d['ts'], utc=True, errors='coerce')
        d = d.dropna(subset=['ts','close'])
        if d.empty:
            return result
        d['wib'] = d['ts'].dt.tz_convert('Asia/Jakarta')
        d = d[d['wib'].dt.date.astype(str) == market_date]
        if d.empty:
            return result
        row = d.sort_values('ts').iloc[-1]
        local_now = pd.Timestamp(session.get('wib'))
        if local_now.tzinfo is None:
            local_now = local_now.tz_localize('Asia/Jakarta')
        age_minutes = max(0.0, (local_now.tz_convert('UTC') - row['ts']).total_seconds() / 60.0)
        stale_limit = max(
            45,
            int(CFG.get('intraday_live_stale_minutes', 45)),
            int(CFG.get('intraday_refresh_minutes', 30)) + 10,
        )
        result.update({
            "fresh": age_minutes <= stale_limit,
            "price": float(row['close']),
            "ts": row['ts'].isoformat(),
            "age_minutes": age_minutes,
        })
        return result
    except Exception:
        try:
            con.close()
        except Exception:
            pass
        return result


def _cached_time_signal(ticker):
    """Use time intelligence only when live intraday evidence is actually fresh."""
    live_bar = _latest_intraday_live_price(str(ticker))
    if live_bar.get('live_required') and not live_bar.get('fresh'):
        return {
            'edge': 0.0,
            'confidence': 0.0,
            'horizon': 'EOD',
            'entry_status': 'NEUTRAL',
            'live_required': True,
            'intraday_fresh': False,
            'intraday_at': str(live_bar.get('ts') or ''),
            'price_source': 'DAILY_FALLBACK_STALE_INTRADAY',
        }
    try:
        tp=app.intraday_prediction(str(ticker))
        if tp is None or tp.empty:
            return {
                'edge':0.0,'confidence':0.0,'horizon':'EOD','entry_status':'NEUTRAL',
                'live_required':bool(live_bar.get('live_required')),
                'intraday_fresh':bool(live_bar.get('fresh')),
                'intraday_at':str(live_bar.get('ts') or ''),
                'price_source':'LIVE_INTRADAY' if live_bar.get('fresh') else 'HISTORICAL_INTRADAY',
            }
        row=tp[tp.horizon.astype(str)=='EOD']
        if row.empty: row=tp.iloc[[0]]
        r=row.iloc[0]
        return {
            'edge':float(r.edge),'confidence':float(r.confidence),'horizon':str(r.horizon),'entry_status':str(r.entry_status),
            'live_required':bool(live_bar.get('live_required')),
            'intraday_fresh':bool(live_bar.get('fresh')),
            'intraday_at':str(live_bar.get('ts') or ''),
            'price_source':'LIVE_INTRADAY' if live_bar.get('fresh') else 'HISTORICAL_INTRADAY',
        }
    except Exception:
        return {
            'edge':0.0,'confidence':0.0,'horizon':'EOD','entry_status':'NEUTRAL',
            'live_required':bool(live_bar.get('live_required')),
            'intraday_fresh':bool(live_bar.get('fresh')),
            'intraday_at':str(live_bar.get('ts') or ''),
            'price_source':'LIVE_INTRADAY' if live_bar.get('fresh') else 'HISTORICAL_INTRADAY',
        }
'''
dyn = dyn[:start] + new_time + dyn[end:]

state_old = "    if mode=='MONITOR_FALLBACK': return 'MONITOR'\n"
state_new = "    if mode=='MONITOR_FALLBACK': return 'MONITOR'\n    if bool(ts.get('live_required')) and not bool(ts.get('intraday_fresh')): return 'MONITOR'\n"
if state_old not in dyn:
    raise SystemExit('dynamic state anchor missing')
dyn = dyn.replace(state_old, state_new, 1)

loop_old = "    for _,r in c.iterrows():\n        ts=_cached_time_signal(r.ticker)\n"
loop_new = "    for _,r in c.iterrows():\n        r=r.copy()\n        live_bar=_latest_intraday_live_price(r.ticker)\n        if live_bar.get('fresh') and live_bar.get('price') is not None:\n            r['close']=float(live_bar['price'])\n        ts=_cached_time_signal(r.ticker)\n"
if loop_old not in dyn:
    raise SystemExit('dynamic loop anchor missing')
dyn = dyn.replace(loop_old, loop_new, 1)

rows_anchor = "            'prev_rank':prev_rank,'prev_score':prev_score,'stable':stable,'candidate_mode':candidate_mode\n"
rows_repl = "            'prev_rank':prev_rank,'prev_score':prev_score,'stable':stable,'candidate_mode':candidate_mode,\n            'price_source':str(ts.get('price_source') or ('LIVE_INTRADAY' if live_bar.get('fresh') else 'DAILY')),\n            'intraday_fresh':bool(ts.get('intraday_fresh')),\n            'intraday_at':str(ts.get('intraday_at') or live_bar.get('ts') or '')\n"
if rows_anchor not in dyn:
    raise SystemExit('dynamic rows anchor missing')
dyn = dyn.replace(rows_anchor, rows_repl, 1)

reason_old = "        reason=f\"{mode_note}score {rr.score:.1f}; IHSG {rr.market_regime} {rr.market_score:.1f}; alignment {rr.ihsg_alignment:.1f}; regime adj {rr.regime_adjustment:+.1f}; relative {rr.relative_percentile*100:.1f}%; time edge {ts.get('edge',0)*100:+.1f}%; {rr.decision}\"\n"
reason_new = "        intraday_note=(f\"intraday FRESH {rr.intraday_at}\" if bool(rr.intraday_fresh) else (\"intraday STALE; live action blocked\" if bool(ts.get('live_required')) else \"intraday historical/off-market\"))\n        reason=f\"{mode_note}score {rr.score:.1f}; IHSG {rr.market_regime} {rr.market_score:.1f}; alignment {rr.ihsg_alignment:.1f}; regime adj {rr.regime_adjustment:+.1f}; relative {rr.relative_percentile*100:.1f}%; time edge {ts.get('edge',0)*100:+.1f}%; {intraday_note}; {rr.decision}\"\n"
if reason_old not in dyn:
    raise SystemExit('dynamic reason anchor missing')
dyn = dyn.replace(reason_old, reason_new, 1)

output_old = "'candidate_mode':rr.candidate_mode})\n"
output_new = "'candidate_mode':rr.candidate_mode,'price_source':rr.price_source,'intraday_fresh':bool(rr.intraday_fresh),'intraday_at':rr.intraday_at})\n"
if output_old not in dyn:
    raise SystemExit('dynamic output anchor missing')
dyn = dyn.replace(output_old, output_new, 1)

state_anchor = "    app.state_set('dynamic_candidate_mode',candidate_mode)\n"
state_repl = "    app.state_set('dynamic_candidate_mode',candidate_mode)\n    app.state_set('dynamic_intraday_fresh_count',sum(1 for item in out if item.get('intraday_fresh')))\n"
if state_anchor not in dyn:
    raise SystemExit('dynamic state-set anchor missing')
dyn = dyn.replace(state_anchor, state_repl, 1)
dyn_path.write_text(dyn, encoding='utf-8')

# Config defaults for bounded refresh/backoff/freshness.
cfg_path = Path('config.json')
cfg = cfg_path.read_text(encoding='utf-8')
config_anchor = '  "intraday_symbols_per_cycle": 60,\n'
if config_anchor not in cfg:
    raise SystemExit('config intraday anchor missing')
config_new = config_anchor + '  "intraday_refresh_period_days": 5,\n  "intraday_refresh_retry_minutes": 10,\n  "intraday_live_stale_minutes": 45,\n'
cfg = cfg.replace(config_anchor, config_new, 1)
cfg_path.write_text(cfg, encoding='utf-8')
print('R29 LIVE INTRADAY FRESHNESS PATCH APPLIED')
