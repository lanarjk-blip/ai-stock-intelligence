from pathlib import Path

p=Path('worker_base.py')
s=p.read_text(encoding='utf-8')

# 1) Isolate collection state namespaces so bootstrap never overwrites full-universe quality metrics.
s=s.replace('def collect_fast(universe):','def collect_fast(universe, state_prefix="last_collection"):',1)
old='''    app.state_set("last_collection", now)\n    app.state_set("last_collection_ok", ok)\n    app.state_set("last_collection_rows", rows)\n    app.state_set(\n        "last_collection_errors",\n        len(errors),\n    )\n'''
new='''    if state_prefix:\n        prefix = str(state_prefix)\n        app.state_set(prefix, now)\n        app.state_set(prefix + "_ok", ok)\n        app.state_set(prefix + "_rows", rows)\n        app.state_set(prefix + "_errors", len(errors))\n'''
if new not in s:
    if old not in s: raise SystemExit('collection state anchor missing')
    s=s.replace(old,new,1)

# 2) Decide when broad daily-data collection is truly due.
marker='''\n\n# ============================================================\n# IHSG / MARKET DATA\n# ============================================================\n'''
helper='''\n\ndef full_collection_status(universe_count):\n    """Return (due, reason) while preserving the last broad quality sample."""\n    total = max(1, int(universe_count or 0))\n    hours = float(CFG.get("full_collect_hours", 6))\n    if stale("last_collection", hours):\n        return True, "STALE_OR_MISSING"\n    try:\n        collected_ok = int(app.state_get("last_collection_ok") or 0)\n        ready_symbols = int(count_ready_symbols(60))\n        price_rows = int(count_price_rows())\n    except Exception:\n        return True, "QUALITY_STATE_INVALID"\n    min_collection = float(CFG.get("data_quality_min_collection_ratio", 0.80))\n    min_ready = float(CFG.get("data_quality_min_ready_ratio", 0.70))\n    min_rows = int(CFG.get("data_quality_min_price_rows", 5000))\n    collection_ratio = collected_ok / total\n    ready_ratio = ready_symbols / total\n    if collection_ratio < min_collection:\n        return True, f"COLLECTION_COVERAGE_{collection_ratio:.3f}"\n    if ready_ratio < min_ready:\n        return True, f"READY_COVERAGE_{ready_ratio:.3f}"\n    if price_rows < min_rows:\n        return True, f"PRICE_ROWS_{price_rows}"\n    return False, "FRESH_CACHE"\n'''
if 'def full_collection_status(' not in s:
    if marker not in s: raise SystemExit('IHSG section marker missing')
    s=s.replace(marker,helper+marker,1)

# 3) Replace always-on bootstrap with true cold-start bootstrap.
start=s.index('''    # ========================================================\n    # PHASE 1\n    # FAST BOOTSTRAP\n    # ========================================================\n''')
end=s.index('''    # ========================================================\n    # PHASE 3\n    # FULL UNIVERSE COLLECTION\n    # ========================================================\n''',start)
bootstrap='''    # ========================================================\n    # PHASE 1-2\n    # COLD-START BOOTSTRAP ONLY\n    # ========================================================\n\n    cold_start = not bool(app.state_get("engine_last_run"))\n    if cold_start:\n        bootstrap_n = min(\n            int(CFG.get("bootstrap_symbols", 30)),\n            len(universe),\n        )\n        bootstrap = universe.head(bootstrap_n).copy()\n        log("BOOTSTRAP", "START", f"symbols={len(bootstrap)}")\n        ok, rows, errors = collect_fast(\n            bootstrap,\n            state_prefix="last_bootstrap_collection",\n        )\n        log(\n            "BOOTSTRAP",\n            "COLLECT_DONE",\n            f"ok={ok}/{len(bootstrap)} rows={rows} errors={len(errors)}",\n        )\n        if errors:\n            log("BOOTSTRAP", "ERROR_SAMPLE", " | ".join(str(err)[:350] for err in errors[:5]))\n        bootstrap_res = analyze_and_publish(bootstrap, "technical-bootstrap", model=None)\n        if not bootstrap_res.empty:\n            log("BOOTSTRAP", "SNAPSHOT_READY", f"count={len(bootstrap_res)}")\n        mark_engine_progress("BOOTSTRAP_MARKET_DATA")\n        refresh_market_data()\n        mark_engine_progress("BOOTSTRAP_MODEL_TRAIN")\n        maybe_train(bootstrap, force=False)\n        mark_engine_progress("BOOTSTRAP_MODEL_DONE")\n        bootstrap_model = load_model()\n        if bootstrap_model is not None:\n            refined = analyze_and_publish(bootstrap, "model-refined-bootstrap", model=bootstrap_model)\n            if not refined.empty:\n                log("MODEL_REFINE", "SNAPSHOT_UPDATED", f"count={len(refined)}")\n    else:\n        log("BOOTSTRAP", "SKIP", "warm runtime; full snapshot already exists")\n\n'''
s=s[:start]+bootstrap+s[end:]

# 4) Full collection is cache-aware and quality-aware.
start=s.index('''    # ========================================================\n    # PHASE 3\n    # FULL UNIVERSE COLLECTION\n    # ========================================================\n''')
end=s.index('''    # ========================================================\n    # PHASE 4\n    # FINAL MODEL TRAINING\n    # ========================================================\n''',start)
full='''    # ========================================================\n    # PHASE 3\n    # FULL UNIVERSE COLLECTION WHEN DUE\n    # ========================================================\n\n    full_due, full_reason = full_collection_status(len(universe))\n    if full_due:\n        app.state_set("full_collection_mode", "FETCH")\n        app.state_set("full_collection_reason", full_reason)\n        log("FULL_COLLECT", "START", f"symbols={len(universe)} reason={full_reason}")\n        ok, rows, errors = collect_fast(universe, state_prefix="last_collection")\n        log(\n            "FULL_COLLECT",\n            "DONE",\n            f"ok={ok}/{len(universe)} rows={rows} errors={len(errors)}",\n        )\n        if errors:\n            log("FULL_COLLECT", "ERROR_SAMPLE", " | ".join(str(err)[:350] for err in errors[:5]))\n    else:\n        app.state_set("full_collection_mode", "CACHE")\n        app.state_set("full_collection_reason", full_reason)\n        ok = int(app.state_get("last_collection_ok") or 0)\n        rows = int(app.state_get("last_collection_rows") or 0)\n        errors = []\n        log(\n            "FULL_COLLECT",\n            "SKIP_FRESH_CACHE",\n            f"reason={full_reason} previous_ok={ok}/{len(universe)} previous_rows={rows}",\n        )\n        mark_engine_progress("FULL_COLLECT_CACHE", f"ok={ok}/{len(universe)} rows={rows}")\n\n'''
s=s[:start]+full+s[end:]

# 5) Model training now honors jst_retrain_hours instead of bypassing it every cycle.
old='''    # Force another attempt because the full collection may\n    # have supplied the data that bootstrap did not have.\n    mark_engine_progress("FULL_MODEL_TRAIN")\n    maybe_train(\n        universe,\n        force=True,\n    )\n'''
new='''    # Train only when the deployed model is missing or stale according to\n    # jst_retrain_hours. A fresh cached model is reused across cycles.\n    mark_engine_progress("FULL_MODEL_TRAIN")\n    maybe_train(\n        universe,\n        force=False,\n    )\n'''
if old not in s: raise SystemExit('forced model training anchor missing')
s=s.replace(old,new,1)

# 6) Market-aware loop cadence uses the existing config knobs.
marker='''\n\n# ============================================================\n# WORKER LOOP\n# ============================================================\n\ndef run_forever():\n'''
helper='''\n\ndef cycle_interval_seconds(now=None):\n    """Use the existing live/post-close/off-market cadence configuration."""\n    session = market_session.session_status(CFG, now=now)\n    status = str(session.get("status") or "").upper()\n    live = bool(session.get("live"))\n    if live or status == "BREAK":\n        minutes = CFG.get("market_open_cycle_minutes", 10)\n    elif status == "CLOSED":\n        minutes = CFG.get("post_close_cycle_minutes", 30)\n    elif status in {"WEEKEND", "HOLIDAY"}:\n        minutes = CFG.get("off_market_cycle_minutes", 60)\n    else:\n        minutes = CFG.get("refresh_minutes", CFG.get("worker_cycle_minutes", 30))\n    return max(5, int(minutes)) * 60\n\n\n# ============================================================\n# WORKER LOOP\n# ============================================================\n\ndef run_forever():\n'''
if 'def cycle_interval_seconds(' not in s:
    if marker not in s: raise SystemExit('worker loop marker missing')
    s=s.replace(marker,helper,1)

old='''def run_forever():\n    interval = max(\n        5,\n        int(\n            CFG.get(\n                "worker_cycle_minutes",\n                30,\n            )\n        ),\n    ) * 60\n\n    print(\n'''
new='''def run_forever():\n    print(\n'''
if old not in s: raise SystemExit('fixed interval block missing')
s=s.replace(old,new,1)
old='''        sleep_for = max(\n            30,\n            interval - elapsed,\n        )\n'''
new='''        interval = cycle_interval_seconds()\n        sleep_for = max(\n            30,\n            interval - elapsed,\n        )\n        app.state_set("engine_next_cycle_interval_seconds", int(interval))\n'''
if old not in s: raise SystemExit('sleep interval anchor missing')
s=s.replace(old,new,1)

p.write_text(s,encoding='utf-8')
print('R26 WORKER EFFICIENCY PATCH APPLIED')
