from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_termux_requirements_exclude_native_heavy_builds():
    text = (ROOT / "requirements-termux.txt").read_text(encoding="utf-8").lower()
    active = [line.strip() for line in text.splitlines() if line.strip() and not line.strip().startswith("#")]
    assert not any(line.startswith("pandas") for line in active)
    assert not any(line.startswith("numpy") for line in active)
    assert not any(line.startswith("pyarrow") for line in active)
    assert not any(line.startswith("xgboost") for line in active)
    assert any(line.startswith("streamlit") for line in active)


def test_termux_launcher_sets_lite_mode_and_never_installs_xgboost():
    start = (ROOT / "ops" / "autostart" / "start_termux_lite.sh").read_text(encoding="utf-8")
    bootstrap = (ROOT / "ops" / "autostart" / "bootstrap_termux_lite.sh").read_text(encoding="utf-8")
    hybrid = (ROOT / "ops" / "autostart" / "start_hybrid_worker.py").read_text(encoding="utf-8")
    assert 'ASI_TERMUX_LITE="1"' in start
    assert "python-pandas" in bootstrap
    assert "python-pyarrow" in bootstrap
    assert "requirements-termux.txt" in bootstrap
    assert "pip install xgboost" not in bootstrap.lower()
    assert 'env["ASI_TERMUX_LITE"] = "1"' in hybrid
