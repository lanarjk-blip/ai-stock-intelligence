from pathlib import Path
import json


def test_runtime_version_not_hardcoded_to_v1848():
    cfg = json.loads(Path("config.json").read_text(encoding="utf-8"))
    app = Path("app.py").read_text(encoding="utf-8")
    assert cfg["engine_version"] == "1.8.52"
    assert cfg["mobile_ui_version"] == "1.8.52"
    assert "AI Stock Intelligence V1.8.48" not in app, "UI title is stale/hard-coded"
