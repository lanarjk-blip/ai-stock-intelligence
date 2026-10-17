from pathlib import Path


def test_android_wrapper_uses_package_version_not_hardcoded_string():
    java = Path("android-app/app/src/main/java/id/asi/stockintelligence/MainActivity.java").read_text(encoding="utf-8")
    assert "BuildConfig.VERSION_NAME" in java
    assert 'APP_VERSION = "1.8.54"' not in java
    assert 'status.setText(appTitle())' in java
    assert 'AIStockIntelligenceAPK/' in java


def test_storage_health_is_explicitly_wib():
    src = Path("storage_housekeeping.py").read_text(encoding="utf-8")
    assert 'ZoneInfo("Asia/Jakarta")' in src
    assert 'strftime("%d-%m %H:%M WIB")' in src


def test_personal_release_scope_is_documented():
    contract = Path("FINAL_PERSONAL_RELEASE.md").read_text(encoding="utf-8")
    assert "single-user personal deployment" in contract
    assert "ADMIN/OPERATOR" in contract
    assert "not a release blocker" in contract
