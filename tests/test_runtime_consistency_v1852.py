from pathlib import Path
import ast
import json


def test_runtime_version_is_normalized_from_config():
    cfg = json.loads(Path("config.json").read_text(encoding="utf-8"))
    ui = Path("dynamic_opportunity_ui.py").read_text(encoding="utf-8")
    assert cfg["engine_version"] == "1.8.52"
    assert cfg["mobile_ui_version"] == "1.8.55"
    assert "mobile_ui_version" in ui
    assert "st.title = title" in ui
    assert "st.set_page_config = set_page_config" in ui


def test_worker_reload_watchdog_exists():
    watchdog = Path("sitecustomize.py").read_text(encoding="utf-8")
    assert "AI_ENGINE_WORKER" in watchdog
    assert "os.execv" in watchdog
    assert "accuracy_guardrails.py" in watchdog
    assert "config.json" in watchdog
    assert "premarket_scheduler.py" in watchdog
    assert "premarket_preliminary.py" in watchdog
    assert "premarket_ui_patch.py" in watchdog
    assert "asi-premarket-h10" in watchdog
    assert "asi-premarket-preliminary" in watchdog


def test_worker_has_guaranteed_premarket_bootstrap_not_only_sitecustomize():
    worker = Path("worker_base.py").read_text(encoding="utf-8")
    guard = Path("accuracy_guardrails.py").read_text(encoding="utf-8")
    ast.parse(guard)

    assert "import accuracy_guardrails" in worker
    assert "_start_worker_premarket_daemons" in guard
    assert 'os.environ.get("AI_ENGINE_WORKER") != "1"' in guard
    assert 'os.path.basename(str(sys.argv[0] or "")) != "worker_base.py"' in guard
    assert 'name="asi-premarket-h10"' in guard
    assert 'name="asi-premarket-preliminary"' in guard
    assert "import premarket_scheduler" in guard
    assert "import premarket_preliminary" in guard
    assert "threading.enumerate()" in guard


def test_premarket_h10_deadline_contract_is_explicit_and_compilable():
    scheduler = Path("premarket_scheduler.py").read_text(encoding="utf-8")
    ast.parse(scheduler)
    assert 'FINAL_LOCK = str(CFG.get("premarket_final_lock", "08:50"))' in scheduler
    assert 'MARKET_OPEN = str(CFG.get("premarket_market_open", "09:00"))' in scheduler
    assert 'return "FINALIZE"' in scheduler
    assert "finalize_h10(local)" in scheduler
    assert "DELETE FROM premarket_picks WHERE market_date=?" in scheduler
    assert "DELETE FROM premarket_runs WHERE market_date=?" in scheduler
    assert "H10_FINAL" in scheduler
    assert "_already_finalized" in scheduler
    assert "MONITOR_FALLBACK" in scheduler
    assert 'c.loc[original.isin({"BUY", "STRONG_BUY"}), "decision"] = "WAIT"' in scheduler
    assert "SELL/AVOID" in scheduler
    assert "premarket_h10_timing" in scheduler


def test_premarket_preliminary_is_always_visible_without_faking_final_lock():
    preliminary = Path("premarket_preliminary.py").read_text(encoding="utf-8")
    ui_patch = Path("premarket_ui_patch.py").read_text(encoding="utf-8")
    ast.parse(preliminary)
    ast.parse(ui_patch)
    assert 'state == "BEFORE"' in preliminary
    assert 'state == "PREPARE"' in preliminary
    assert 'state == "LIGHT"' in preliminary
    assert 'state == "OFF"' in preliminary
    assert '== "CLOSED"' in preliminary
    assert '"PRELIMINARY"' in preliminary
    assert "_already_finalized" in preliminary
    assert "H10_FINAL" in preliminary
    assert '"FINAL_EXISTS"' in preliminary
    assert "select_candidates" in preliminary
    assert "premarket-preliminary" in preliminary
    assert '"PRELIMINARY"' in ui_patch
    assert "belum final H-10" in preliminary
    assert "Daftar ini masih boleh berubah" in ui_patch
    assert "08:50 WIB" in ui_patch
