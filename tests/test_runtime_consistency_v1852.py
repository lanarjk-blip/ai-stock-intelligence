from pathlib import Path
import ast
import json


def test_runtime_version_is_normalized_from_config():
    cfg = json.loads(Path("config.json").read_text(encoding="utf-8"))
    ui = Path("dynamic_opportunity_ui.py").read_text(encoding="utf-8")
    assert cfg["engine_version"] == "1.8.52"
    assert cfg["mobile_ui_version"] == "1.8.52"
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
    assert "asi-premarket-h10" in watchdog


def test_premarket_h10_deadline_contract_is_explicit_and_compilable():
    scheduler = Path("premarket_scheduler.py").read_text(encoding="utf-8")
    ast.parse(scheduler)

    # Deadline must be independent of the ordinary 30-minute worker cycle.
    assert 'FINAL_LOCK = str(CFG.get("premarket_final_lock", "08:50"))' in scheduler
    assert 'MARKET_OPEN = str(CFG.get("premarket_market_open", "09:00"))' in scheduler
    assert 'return "FINALIZE"' in scheduler
    assert "finalize_h10(local)" in scheduler

    # Any provisional night/early-morning run may be replaced at H-10, but the
    # final H-10 run itself is protected from hindsight rewrites.
    assert "DELETE FROM premarket_picks WHERE market_date=?" in scheduler
    assert "DELETE FROM premarket_runs WHERE market_date=?" in scheduler
    assert "H10_FINAL" in scheduler
    assert "_already_finalized" in scheduler

    # If no strict BUY passes, the UI may still show useful candidates, but the
    # scheduler must demote failed BUY rows instead of fabricating a BUY signal.
    assert "MONITOR_FALLBACK" in scheduler
    assert 'c.loc[original.isin({"BUY", "STRONG_BUY"}), "decision"] = "WAIT"' in scheduler
    assert "SELL/AVOID" in scheduler
    assert "premarket_h10_timing" in scheduler
