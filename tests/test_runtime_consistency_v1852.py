from pathlib import Path
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
