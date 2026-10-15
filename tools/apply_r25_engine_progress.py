from pathlib import Path

# ---------- config ----------
p=Path('config.json')
s=p.read_text(encoding='utf-8')
anchor='  "runtime_heartbeat_max_age_seconds": 180,\n'
addition='  "runtime_heartbeat_max_age_seconds": 180,\n  "engine_progress_max_age_seconds": 1200,\n  "engine_result_max_age_minutes": 90,\n'
if addition not in s:
    if anchor not in s: raise SystemExit('config runtime heartbeat anchor missing')
    s=s.replace(anchor,addition,1)
p.write_text(s,encoding='utf-8')

# ---------- worker progress instrumentation ----------
p=Path('worker_base.py')
s=p.read_text(encoding='utf-8')
anchor='''def stale(key, hours):\n    value = get_last(key)\n'''
if anchor not in s: raise SystemExit('worker stale anchor missing')
# insert helper before data collection section, after stale() function by using next section marker
marker='''\n\n# ============================================================\n# DATA COLLECTION\n# ============================================================\n'''
helper='''\n\ndef mark_engine_progress(stage, detail=""):\n    """Persist evidence that the main engine loop is actively advancing."""\n    now = datetime.now(timezone.utc).isoformat()\n    app.state_set("engine_progress_at", now)\n    app.state_set("engine_progress_stage", str(stage or "UNKNOWN"))\n    app.state_set("engine_cycle_status", "RUNNING")\n    if detail:\n        app.state_set("engine_progress_detail", str(detail)[:500])\n    return now\n'''
if 'def mark_engine_progress(' not in s:
    if marker not in s: raise SystemExit('worker data section marker missing')
    s=s.replace(marker,helper+marker,1)

old='''    ok = 0\n    rows = 0\n    errors = []\n'''
new='''    ok = 0\n    rows = 0\n    errors = []\n    completed = 0\n    last_progress = time.monotonic()\n    mark_engine_progress("COLLECT_START", f"symbols={len(tickers)}")\n'''
if new not in s:
    if old not in s: raise SystemExit('collect counters anchor missing')
    s=s.replace(old,new,1)

old='''                if err:\n                    errors.append(\n                        f"{ticker}: {err}"\n                    )\n\n            except Exception as exc:\n                errors.append(str(exc)[:300])\n\n    now = datetime.now(timezone.utc).isoformat()\n'''
new='''                if err:\n                    errors.append(\n                        f"{ticker}: {err}"\n                    )\n                completed += 1\n                current_progress = time.monotonic()\n                if (\n                    completed == len(tickers)\n                    or completed % 25 == 0\n                    or current_progress - last_progress >= 30\n                ):\n                    mark_engine_progress(\n                        "COLLECT_PROGRESS",\n                        f"completed={completed}/{len(tickers)} ok={ok} rows={rows}",\n                    )\n                    last_progress = current_progress\n\n            except Exception as exc:\n                completed += 1\n                errors.append(str(exc)[:300])\n                current_progress = time.monotonic()\n                if completed % 25 == 0 or current_progress - last_progress >= 30:\n                    mark_engine_progress(\n                        "COLLECT_PROGRESS",\n                        f"completed={completed}/{len(tickers)} ok={ok} rows={rows}",\n                    )\n                    last_progress = current_progress\n\n    now = datetime.now(timezone.utc).isoformat()\n    mark_engine_progress("COLLECT_DONE", f"completed={completed}/{len(tickers)} ok={ok} rows={rows}")\n'''
if new not in s:
    if old not in s: raise SystemExit('collect progress anchor missing')
    s=s.replace(old,new,1)

old='''def cycle():\n    ensure_tables()\n    try:\n'''
new='''def cycle():\n    ensure_tables()\n    cycle_started = datetime.now(timezone.utc).isoformat()\n    app.state_set("engine_cycle_started_at", cycle_started)\n    app.state_set("engine_cycle_status", "RUNNING")\n    app.state_set("engine_cycle_error", "")\n    mark_engine_progress("CYCLE_START")\n    try:\n'''
if new not in s:
    if old not in s: raise SystemExit('cycle start anchor missing')
    s=s.replace(old,new,1)

repls=[
('''    refresh_market_data()\n\n    maybe_train(\n        bootstrap,\n        force=False,\n    )\n''','''    mark_engine_progress("BOOTSTRAP_MARKET_DATA")\n    refresh_market_data()\n\n    mark_engine_progress("BOOTSTRAP_MODEL_TRAIN")\n    maybe_train(\n        bootstrap,\n        force=False,\n    )\n    mark_engine_progress("BOOTSTRAP_MODEL_DONE")\n'''),
('''    refresh_market_data()\n\n    # Force another attempt because the full collection may\n''','''    mark_engine_progress("FULL_MARKET_DATA")\n    refresh_market_data()\n\n    # Force another attempt because the full collection may\n'''),
('''    maybe_train(\n        universe,\n        force=True,\n    )\n\n    model = load_model()\n''','''    mark_engine_progress("FULL_MODEL_TRAIN")\n    maybe_train(\n        universe,\n        force=True,\n    )\n    mark_engine_progress("FULL_MODEL_DONE")\n\n    model = load_model()\n'''),
('''        maybe_walk_forward(\n            universe,\n            force=False,\n        )\n''','''        mark_engine_progress("WALK_FORWARD")\n        maybe_walk_forward(\n            universe,\n            force=False,\n        )\n        mark_engine_progress("WALK_FORWARD_DONE")\n'''),
('''    result = analyze_and_publish(\n        universe,\n        "full-universe",\n        model=model,\n    )\n''','''    mark_engine_progress("FULL_ANALYSIS")\n    result = analyze_and_publish(\n        universe,\n        "full-universe",\n        model=model,\n    )\n    mark_engine_progress("FULL_ANALYSIS_DONE", f"results={len(result)}")\n''')]
for old,new in repls:
    if new in s: continue
    if old not in s: raise SystemExit('worker phase anchor missing: '+old[:50])
    s=s.replace(old,new,1)

old='''    app.state_set(\n        "engine_last_run",\n        now,\n    )\n'''
new='''    app.state_set(\n        "engine_last_run",\n        now,\n    )\n    app.state_set("engine_cycle_completed_at", now)\n    app.state_set("engine_cycle_status", "IDLE")\n    app.state_set("engine_progress_at", now)\n    app.state_set("engine_progress_stage", "CYCLE_DONE")\n'''
if new not in s:
    if old not in s: raise SystemExit('engine last run anchor missing')
    s=s.replace(old,new,1)

old='''        except Exception:\n            detail = traceback.format_exc()\n\n            print(\n                detail,\n                flush=True,\n            )\n\n            try:\n                log(\n                    "ENGINE",\n                    "ERROR",\n                    detail[-1800:],\n                )\n            except Exception:\n                pass\n'''
new='''        except Exception:\n            detail = traceback.format_exc()\n\n            print(\n                detail,\n                flush=True,\n            )\n\n            try:\n                failed_at = datetime.now(timezone.utc).isoformat()\n                app.state_set("engine_cycle_status", "ERROR")\n                app.state_set("engine_cycle_error_at", failed_at)\n                app.state_set("engine_cycle_error", detail[-900:])\n                app.state_set("engine_progress_at", failed_at)\n                app.state_set("engine_progress_stage", "CYCLE_ERROR")\n                log(\n                    "ENGINE",\n                    "ERROR",\n                    detail[-1800:],\n                )\n            except Exception:\n                pass\n'''
if new not in s:
    if old not in s: raise SystemExit('run loop error anchor missing')
    s=s.replace(old,new,1)
p.write_text(s,encoding='utf-8')

# ---------- shared engine freshness evaluator ----------
p=Path('local_runtime_health.py')
s=p.read_text(encoding='utf-8')
s=s.replace('''        rows = con.execute(\n            "SELECT key,value FROM state WHERE key LIKE 'local_worker_%'"\n        ).fetchall()\n''','''        rows = con.execute(\n            "SELECT key,value FROM state "\n            "WHERE key LIKE 'local_worker_%' OR key LIKE 'engine_%'"\n        ).fetchall()\n''')
marker='''\n\ndef health(now=None, max_age_seconds=None):\n'''
engine_helper='''\n\ndef _age_seconds(value, current):\n    if not value:\n        return None\n    try:\n        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))\n        if dt.tzinfo is None:\n            dt = dt.replace(tzinfo=timezone.utc)\n        return max(0.0, (current.astimezone(timezone.utc) - dt.astimezone(timezone.utc)).total_seconds())\n    except Exception:\n        return None\n\n\ndef engine_progress_health(state, now=None, cfg=None, status_prefix="LOCAL_"):\n    """Validate that the main cycle is making progress, not merely heartbeating."""\n    cfg = cfg or _cfg()\n    current = now or datetime.now(timezone.utc)\n    if current.tzinfo is None:\n        current = current.replace(tzinfo=timezone.utc)\n    progress_limit = max(300, int(cfg.get("engine_progress_max_age_seconds", 1200)))\n    result_limit = max(30, int(cfg.get("engine_result_max_age_minutes", 90))) * 60\n    status = str(state.get("engine_cycle_status") or "").upper().strip()\n    last_run = str(state.get("engine_last_run") or "").strip()\n    progress_at = str(state.get("engine_progress_at") or "").strip()\n    stage = str(state.get("engine_progress_stage") or "").strip()\n    last_age = _age_seconds(last_run, current)\n    progress_age = _age_seconds(progress_at, current)\n    base = {\n        "engine_cycle_status": status or "UNKNOWN",\n        "engine_last_run": last_run,\n        "engine_last_run_age_seconds": last_age,\n        "engine_progress_at": progress_at,\n        "engine_progress_age_seconds": progress_age,\n        "engine_progress_stage": stage,\n        "engine_progress_max_age_seconds": progress_limit,\n        "engine_result_max_age_seconds": result_limit,\n    }\n    if status == "ERROR":\n        return {**base, "ready": False, "status": status_prefix + "ENGINE_ERROR", "reason": "main engine cycle ended in ERROR"}\n    if status == "RUNNING":\n        if progress_age is None:\n            return {**base, "ready": False, "status": status_prefix + "ENGINE_WARMING", "reason": "engine cycle started but no progress marker is available yet"}\n        if progress_age > progress_limit:\n            return {**base, "ready": False, "status": status_prefix + "ENGINE_STALLED", "reason": f"engine progress age {progress_age:.0f}s > {progress_limit}s"}\n        if last_age is None:\n            return {**base, "ready": False, "status": status_prefix + "ENGINE_WARMING", "reason": "first full engine cycle has not completed yet"}\n        if last_age > result_limit:\n            return {**base, "ready": False, "status": status_prefix + "ENGINE_REFRESHING_STALE", "reason": f"last completed engine result age {last_age:.0f}s > {result_limit}s"}\n        return {**base, "ready": True, "status": status_prefix + "RUNTIME_READY_REFRESHING", "reason": ""}\n    if last_age is None:\n        return {**base, "ready": False, "status": status_prefix + "ENGINE_WARMING", "reason": "no completed engine cycle is available"}\n    if last_age > result_limit:\n        return {**base, "ready": False, "status": status_prefix + "ENGINE_STALE", "reason": f"last completed engine result age {last_age:.0f}s > {result_limit}s"}\n    if status not in {"IDLE", ""}:\n        return {**base, "ready": False, "status": status_prefix + "ENGINE_INVALID", "reason": f"unknown engine cycle status: {status}"}\n    return {**base, "ready": True, "status": status_prefix + "RUNTIME_READY", "reason": ""}\n'''
if 'def engine_progress_health(' not in s:
    if marker not in s: raise SystemExit('local health marker missing')
    s=s.replace(marker,engine_helper+marker,1)

old='''    if age > limit:\n        return {"ready": False, "status": "LOCAL_RUNTIME_STALE", "age_seconds": age, "reason": f"local worker heartbeat age {age:.0f}s > {limit}s", "expected_fingerprint": expected, "worker_fingerprint": worker_fp}\n    return {"ready": True, "status": "LOCAL_RUNTIME_READY", "age_seconds": age, "heartbeat_at": heartbeat, "expected_fingerprint": expected, "worker_fingerprint": worker_fp, "build": state.get("local_worker_runtime_build", ""), "generation": state.get("local_worker_generation", ""), "pid": state.get("local_worker_pid", "")}\n'''
new='''    if age > limit:\n        return {"ready": False, "status": "LOCAL_RUNTIME_STALE", "age_seconds": age, "reason": f"local worker heartbeat age {age:.0f}s > {limit}s", "expected_fingerprint": expected, "worker_fingerprint": worker_fp}\n    engine = engine_progress_health(state, now=current, cfg=cfg, status_prefix="LOCAL_")\n    common = {"age_seconds": age, "heartbeat_at": heartbeat, "expected_fingerprint": expected, "worker_fingerprint": worker_fp, "build": state.get("local_worker_runtime_build", ""), "generation": state.get("local_worker_generation", ""), "pid": state.get("local_worker_pid", "")}\n    if not engine.get("ready"):\n        return {**common, **engine}\n    return {**common, **engine}\n'''
if new not in s:
    if old not in s: raise SystemExit('local health return anchor missing')
    s=s.replace(old,new,1)
p.write_text(s,encoding='utf-8')

# ---------- external runtime health uses the same engine progress guard ----------
p=Path('runtime_health.py')
s=p.read_text(encoding='utf-8')
old='''        rows = con.execute(\n            "SELECT key,value FROM state WHERE key IN "\n            "('remote_worker_runtime_build','remote_worker_generation',"\n            "'remote_worker_pid','remote_worker_heartbeat_at','remote_worker_bridge_version')"\n        ).fetchall()\n'''
new='''        rows = con.execute(\n            "SELECT key,value FROM state WHERE key IN "\n            "('remote_worker_runtime_build','remote_worker_generation',"\n            "'remote_worker_pid','remote_worker_heartbeat_at','remote_worker_bridge_version',"\n            "'engine_last_run','engine_cycle_started_at','engine_cycle_completed_at',"\n            "'engine_cycle_status','engine_progress_at','engine_progress_stage','engine_cycle_error_at')"\n        ).fetchall()\n'''
if new not in s:
    if old not in s: raise SystemExit('remote state query anchor missing')
    s=s.replace(old,new,1)
old='''    ready = age <= float(limit)\n    try:\n        pid = int(state.get("remote_worker_pid") or 0)\n    except Exception:\n        pid = 0\n    return {\n        "required": True,\n        "ready": ready,\n        "status": "RUNTIME_READY" if ready else "RUNTIME_STALE",\n        "age_seconds": age,\n        "max_age_seconds": limit,\n        "heartbeat_at": heartbeat,\n        "build": state.get("remote_worker_runtime_build", ""),\n        "generation": state.get("remote_worker_generation", ""),\n        "pid": pid,\n        "reason": "" if ready else f"remote worker heartbeat age {age:.0f}s > {limit}s",\n    }\n'''
new='''    heartbeat_ready = age <= float(limit)\n    try:\n        pid = int(state.get("remote_worker_pid") or 0)\n    except Exception:\n        pid = 0\n    common = {\n        "required": True,\n        "age_seconds": age,\n        "max_age_seconds": limit,\n        "heartbeat_at": heartbeat,\n        "build": state.get("remote_worker_runtime_build", ""),\n        "generation": state.get("remote_worker_generation", ""),\n        "pid": pid,\n    }\n    if not heartbeat_ready:\n        return {**common, "ready": False, "status": "RUNTIME_STALE", "reason": f"remote worker heartbeat age {age:.0f}s > {limit}s"}\n    try:\n        import local_runtime_health\n        engine = local_runtime_health.engine_progress_health(state, now=now, cfg=cfg, status_prefix="RUNTIME_")\n    except Exception as exc:\n        return {**common, "ready": False, "status": "RUNTIME_ENGINE_INVALID", "reason": f"engine progress guard failed: {exc}"}\n    return {**common, **engine}\n'''
if new not in s:
    if old not in s: raise SystemExit('remote health return anchor missing')
    s=s.replace(old,new,1)
p.write_text(s,encoding='utf-8')

# ---------- checkpoint sidecar hygiene ----------
p=Path('persistent_bridge.py')
s=p.read_text(encoding='utf-8')
marker='''\ndef _restore_sqlite_blob(sqlite_gzip, expected_sha):\n'''
helper='''\ndef _remove_sqlite_sidecars(path=None):\n    """Remove stale WAL/SHM sidecars before replacing a cold SQLite database."""\n    base = path or LOCAL_DB\n    removed = []\n    for suffix in ("-wal", "-shm"):\n        sidecar = base + suffix\n        try:\n            os.remove(sidecar)\n            removed.append(sidecar)\n        except FileNotFoundError:\n            pass\n    return removed\n\n'''
if 'def _remove_sqlite_sidecars(' not in s:
    if marker not in s: raise SystemExit('restore sqlite marker missing')
    s=s.replace(marker,helper+marker,1)
old='''        if not _validate_sqlite_file(temp_path):\n            raise ValueError("worker checkpoint SQLite quick_check failed")\n        os.replace(temp_path, LOCAL_DB)\n'''
new='''        if not _validate_sqlite_file(temp_path):\n            raise ValueError("worker checkpoint SQLite quick_check failed")\n        _remove_sqlite_sidecars(LOCAL_DB)\n        os.replace(temp_path, LOCAL_DB)\n        _remove_sqlite_sidecars(LOCAL_DB)\n'''
if new not in s:
    if old not in s: raise SystemExit('sqlite replace anchor missing')
    s=s.replace(old,new,1)
p.write_text(s,encoding='utf-8')

# ---------- heartbeat after persistence restore ----------
p=Path('sitecustomize.py')
s=p.read_text(encoding='utf-8')
marker='''except Exception as exc:\n    print(f"[PERSIST_BRIDGE] startup degraded: {exc!r}", flush=True)\n\n_WATCH = (\n'''
new='''except Exception as exc:\n    print(f"[PERSIST_BRIDGE] startup degraded: {exc!r}", flush=True)\n\n# Persistence restore can overlay old local_worker_* state from a checkpoint or\n# control plane. Re-assert this process identity synchronously before UI health\n# checks or background heartbeat threads rely on that state.\nif _IS_REAL_WORKER:\n    try:\n        import local_runtime_health\n        _identity = dict(_RUNTIME_IDENTITY or {})\n        local_runtime_health.write_worker_heartbeat(\n            pid=os.getpid(),\n            generation=_identity.get("generation", ""),\n            build=_identity.get("build", ""),\n        )\n    except Exception as exc:\n        print(f"[LOCAL_RUNTIME_HEALTH] post-restore heartbeat error: {exc!r}", flush=True)\n\n_WATCH = (\n'''
if new not in s:
    if marker not in s: raise SystemExit('sitecustomize persistence marker missing')
    s=s.replace(marker,new,1)
p.write_text(s,encoding='utf-8')

# ---------- clearer stale-engine UI text ----------
p=Path('runtime_health_ui_patch.py')
s=p.read_text(encoding='utf-8')
old='''                age = health.get("age_seconds")\n                age_text = "belum ada heartbeat" if age is None else f"heartbeat terakhir {age:.0f} detik lalu"\n                st.warning(\n                    "🛡️ **RUNTIME TIDAK SEGAR • Pre-Market dibekukan sebagai data lama.** "\n                    f"{age_text}. Sistem tidak membuat atau meng-upgrade kandidat sampai worker Render kembali sehat."\n                )\n'''
new='''                age = health.get("age_seconds")\n                age_text = "belum ada heartbeat" if age is None else f"heartbeat terakhir {age:.0f} detik lalu"\n                reason = str(health.get("reason") or age_text)\n                st.warning(\n                    "🛡️ **RUNTIME TIDAK SEGAR • Pre-Market dibekukan sebagai data lama.** "\n                    f"{reason}. Sistem tidak membuat atau meng-upgrade kandidat sampai worker kembali sehat."\n                )\n'''
if new not in s:
    if old not in s: raise SystemExit('runtime health UI warning anchor missing')
    s=s.replace(old,new,1)
p.write_text(s,encoding='utf-8')

print('R25 ENGINE PROGRESS + RESTORE HYGIENE PATCH APPLIED')
