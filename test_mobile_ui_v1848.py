import json
from pathlib import Path


def test_mobile_bottom_nav_and_ihsg_chart_present():
    s=Path("app.py").read_text(encoding="utf-8")
    cfg=json.loads(Path("config.json").read_text(encoding="utf-8"))
    expected=str(cfg.get("mobile_ui_version") or cfg.get("engine_version") or "")
    assert expected
    assert "position: fixed !important" in s
    assert "bottom: 0" in s
    assert "Grafik IHSG" in s
    assert "ih_chart_df=fetch_ihsg().copy()" in s
    assert "mobile_ui_version" in s
    assert "AI Stock Intelligence V1.8.48" not in s


def test_historical_data_not_fabricated_per_second():
    s=Path("app.py").read_text(encoding="utf-8")
    assert "Titik historis tidak dibuat-buat menjadi data per detik" in s
