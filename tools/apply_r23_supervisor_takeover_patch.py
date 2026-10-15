from pathlib import Path

# ---------------- local_runtime_health: fingerprint every live-critical module ----------------
p=Path('local_runtime_health.py'); s=p.read_text(encoding='utf-8')
old='''FILES = (\n    "app.py", "worker_base.py", "accuracy_guardrails.py", "dynamic_engine.py",\n    "dynamic_opportunity_ui.py", "market_session.py", "premarket_scheduler.py",\n    "premarket_preliminary.py", "runtime_supervisor.py", "sitecustomize.py",\n)\n'''
new='''FILES = (\n    "app.py", "worker_base.py", "accuracy_guardrails.py", "dynamic_engine.py",\n    "dynamic_opportunity_ui.py", "top5_timeline.py", "recommendation_history.py",\n    "market_session.py", "premarket_scheduler.py", "premarket_preliminary.py",\n    "premarket_intelligence.py", "premarket_evaluation.py", "premarket_explain.py",\n    "closing_overnight.py", "hold_engine.py", "ai_stock_ask.py", "fcm_adapter.py",\n    "runtime_supervisor.py", "runtime_health.py", "runtime_health_ui_patch.py",\n    "persistent_bridge.py", "ui_replica.py", "replica_runtime.py",\n    "external_runtime_guard.py", "runtime_api.py", "sitecustomize.py", "config.json",\n    "data/idx_holidays.json",\n)\n'''
if old in s: s=s.replace(old,new,1)
elif new not in s: raise SystemExit('local fingerprint FILES anchor missing')
p.write_text(s,encoding='utf-8')

# ---------------- sitecustomize: watch the same critical modules, hash content ----------------
p=Path('sitecustomize.py'); s=p.read_text(encoding='utf-8')
old='''    "dynamic_engine.py",\n    "dynamic_opportunity_ui.py",\n    "market_session.py",\n'''
new='''    "dynamic_engine.py",\n    "dynamic_opportunity_ui.py",\n    "top5_timeline.py",\n    "recommendation_history.py",\n    "hold_engine.py",\n    "ai_stock_ask.py",\n    "closing_overnight.py",\n    "fcm_adapter.py",\n    "market_session.py",\n'''
if old in s: s=s.replace(old,new,1)
elif new not in s: raise SystemExit('site watch dynamic anchor missing')
old='''    "premarket_scheduler.py",\n    "premarket_preliminary.py",\n    "premarket_ui_patch.py",\n'''
new='''    "premarket_scheduler.py",\n    "premarket_preliminary.py",\n    "premarket_intelligence.py",\n    "premarket_evaluation.py",\n    "premarket_explain.py",\n    "premarket_ui_patch.py",\n'''
if old in s: s=s.replace(old,new,1)
elif new not in s: raise SystemExit('site watch premarket anchor missing')
old='''def _fingerprint():\n    h = hashlib.sha256()\n    for name in _WATCH:\n        path = os.path.join(_BASE, name)\n        h.update(name.encode("utf-8"))\n        try:\n            stat = os.stat(path)\n            h.update(str(stat.st_mtime_ns).encode("ascii"))\n            h.update(str(stat.st_size).encode("ascii"))\n        except OSError:\n            h.update(b"missing")\n    return h.hexdigest()\n'''
new='''def _fingerprint():\n    """Hash source content so same-size edits cannot escape worker reload."""\n    h = hashlib.sha256()\n    for name in _WATCH:\n        path = os.path.join(_BASE, name)\n        h.update(name.encode("utf-8"))\n        try:\n            with open(path, "rb") as fh:\n                while True:\n                    chunk = fh.read(1024 * 1024)\n                    if not chunk:\n                        break\n                    h.update(chunk)\n        except OSError:\n            h.update(b"missing")\n    return h.hexdigest()\n'''
if old in s: s=s.replace(old,new,1)
elif new not in s: raise SystemExit('site fingerprint anchor missing')
p.write_text(s,encoding='utf-8')

# ---------------- runtime_supervisor: stale owner takeover ----------------
p=Path('runtime_supervisor.py'); s=p.read_text(encoding='utf-8')
old='''STARTED_AT = datetime.now(timezone.utc).isoformat()\n\n\ndef is_real_worker() -> bool:\n'''
new='''STARTED_AT = datetime.now(timezone.utc).isoformat()\ntry:\n    from local_runtime_health import source_fingerprint as _source_fingerprint\n    SOURCE_FINGERPRINT = _source_fingerprint()\nexcept Exception:\n    SOURCE_FINGERPRINT = "unknown"\n\n\ndef is_real_worker() -> bool:\n'''
if old in s: s=s.replace(old,new,1)
elif new not in s: raise SystemExit('supervisor fingerprint anchor missing')

anchor='''def _retire_stale_workers():\n'''
helpers='''def _read_lock_identity(handle):\n    try:\n        handle.seek(0)\n        raw = handle.read().strip()\n    except Exception:\n        raw = ""\n    out = {}\n    for token in raw.split():\n        if "=" in token:\n            key, value = token.split("=", 1)\n            out[key] = value\n    try:\n        out["pid"] = int(out.get("pid") or 0)\n    except Exception:\n        out["pid"] = 0\n    return out\n\n\ndef _pid_is_verified_worker(pid):\n    try:\n        pid = int(pid or 0)\n        if pid <= 0 or pid == os.getpid() or os.name != "posix":\n            return False\n        proc_dir = f"/proc/{pid}"\n        if not os.path.isdir(proc_dir):\n            return False\n        if hasattr(os, "getuid") and os.stat(proc_dir).st_uid != os.getuid():\n            return False\n        with open(os.path.join(proc_dir, "cmdline"), "rb") as fh:\n            cmd = fh.read().replace(b"\\x00", b" ").decode("utf-8", "ignore")\n        return "worker_base.py" in cmd\n    except Exception:\n        return False\n\n\ndef _should_takeover(identity):\n    """Only replace the verified lock owner when its startup fingerprint is stale."""\n    pid = int((identity or {}).get("pid") or 0)\n    if not _pid_is_verified_worker(pid):\n        return False\n    owner_fp = str((identity or {}).get("fingerprint") or "").strip()\n    # Legacy supervisors did not record a fingerprint, so one controlled\n    # takeover is required when rolling out this contract.\n    return not owner_fp or owner_fp != SOURCE_FINGERPRINT\n\n\ndef _retire_lock_owner(identity, timeout=3.0):\n    pid = int((identity or {}).get("pid") or 0)\n    if not _pid_is_verified_worker(pid):\n        return False\n    try:\n        os.kill(pid, signal.SIGTERM)\n    except ProcessLookupError:\n        return True\n    except Exception:\n        return False\n    deadline = time.monotonic() + max(0.2, float(timeout))\n    while time.monotonic() < deadline:\n        if not _pid_is_verified_worker(pid):\n            return True\n        time.sleep(0.1)\n    # A detached stale worker that ignores SIGTERM must not permanently block\n    # the new deploy. PID/cmdline/uid were verified above before escalation.\n    try:\n        os.kill(pid, signal.SIGKILL)\n    except ProcessLookupError:\n        return True\n    except Exception:\n        return False\n    time.sleep(0.2)\n    return not _pid_is_verified_worker(pid)\n\n\n'''
if helpers not in s:
    if anchor not in s: raise SystemExit('supervisor helper anchor missing')
    s=s.replace(anchor,helpers+anchor,1)

old='''    handle = open(LOCK_PATH, "a+")\n    try:\n        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)\n    except BlockingIOError:\n        handle.close()\n        print(\n            f"[RUNTIME_SUPERVISOR] DUPLICATE_EXIT generation={GENERATION}",\n            flush=True,\n        )\n        os._exit(0)\n\n    LOCK_HANDLE = handle\n'''
new='''    handle = open(LOCK_PATH, "a+")\n    acquired = False\n    try:\n        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)\n        acquired = True\n    except BlockingIOError:\n        owner = _read_lock_identity(handle)\n        if _should_takeover(owner):\n            print(\n                f"[RUNTIME_SUPERVISOR] TAKEOVER stale_pid={owner.get('pid',0)} "\n                f"old_fp={owner.get('fingerprint','legacy')} new_fp={SOURCE_FINGERPRINT}",\n                flush=True,\n            )\n            _retire_lock_owner(owner)\n            deadline = time.monotonic() + 2.0\n            while time.monotonic() < deadline and not acquired:\n                try:\n                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)\n                    acquired = True\n                except BlockingIOError:\n                    time.sleep(0.1)\n        if not acquired:\n            handle.close()\n            print(\n                f"[RUNTIME_SUPERVISOR] DUPLICATE_EXIT generation={GENERATION} "\n                f"owner_pid={owner.get('pid',0)} owner_fp={owner.get('fingerprint','')}",\n                flush=True,\n            )\n            os._exit(0)\n\n    LOCK_HANDLE = handle\n'''
if old in s: s=s.replace(old,new,1)
elif new not in s: raise SystemExit('supervisor claim anchor missing')

old='''        handle.write(\n            f"pid={os.getpid()} generation={GENERATION} build={RUNTIME_BUILD} started={STARTED_AT}\\n"\n        )\n'''
new='''        handle.write(\n            f"pid={os.getpid()} generation={GENERATION} build={RUNTIME_BUILD} "\n            f"fingerprint={SOURCE_FINGERPRINT} started={STARTED_AT}\\n"\n        )\n'''
if old in s: s=s.replace(old,new,1)
elif new not in s: raise SystemExit('supervisor lock write anchor missing')
old='''        "build": RUNTIME_BUILD,\n        "deploy_sha": _DEPLOY_SHA,\n'''
new='''        "build": RUNTIME_BUILD,\n        "fingerprint": SOURCE_FINGERPRINT,\n        "deploy_sha": _DEPLOY_SHA,\n'''
if old in s: s=s.replace(old,new,1)
elif new not in s: raise SystemExit('supervisor claim result anchor missing')
p.write_text(s,encoding='utf-8')

print('R23 SUPERVISOR TAKEOVER PATCH APPLIED')
