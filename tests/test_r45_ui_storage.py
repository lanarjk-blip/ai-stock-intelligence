from pathlib import Path
import ast
import json


def test_r45_config_and_single_source_android_versioning():
    cfg = json.loads(Path('config.json').read_text(encoding='utf-8'))
    assert cfg['mobile_ui_version'] == '1.8.57'
    assert cfg['production_stabilization_version'] == '1.8.57'
    assert cfg['housekeeping_enabled'] is True
    assert cfg['housekeeping_intraday_retention_days'] == 45
    assert cfg['housekeeping_engine_log_retention_days'] == 21
    gradle = Path('android-app/app/build.gradle').read_text(encoding='utf-8')
    assert "new JsonSlurper().parse(file('../../config.json'))" in gradle
    assert 'appConfig.mobile_ui_version' in gradle
    assert 'versionName appVersion' in gradle


def test_r45_semantic_table_colors_and_timestamp_formatting_exist():
    ui = Path('ui_theme_patch.py').read_text(encoding='utf-8')
    ast.parse(ui)
    for token in ('UP %', 'Sideways %', 'DOWN %', 'Mean Return %', 'Confidence %', 'Samples'):
        assert token in ui
    assert 'format_wib' in ui
    assert '_asi_semantic_colors' in ui
    ask = Path('ai_stock_ask.py').read_text(encoding='utf-8')
    assert 'ui_theme_patch.install()' in ask


def test_r45_housekeeping_is_allowlist_only_and_protects_business_tables():
    hk = Path('storage_housekeeping.py').read_text(encoding='utf-8')
    ast.parse(hk)
    assert 'DISPOSABLE_RULES' in hk
    assert '"intraday"' in hk
    assert '"engine_log"' in hk
    for protected in ('recommendation_history', 'shadow_portfolio', 'premarket_picks', 'dynamic_opportunities', 'prices'):
        assert protected in hk
    assert 'PRAGMA quick_check' in hk
    assert 'src.backup(dst)' in hk
    assert 'BEGIN IMMEDIATE' in hk
    assert 'VACUUM' in hk
    assert 'dry_run' in hk


def test_r45_worker_bootstraps_housekeeping_and_selfcheck():
    guard = Path('accuracy_guardrails.py').read_text(encoding='utf-8')
    assert 'asi-storage-housekeeping' in guard
    assert 'runtime_selfcheck.run()' in guard
    check = Path('runtime_selfcheck.py').read_text(encoding='utf-8')
    ast.parse(check)
    assert 'PRAGMA quick_check' in check
    assert 'startup_selfcheck_status' in check


def test_r45_workflow_names_artifact_from_config_version():
    wf = Path('.github/workflows/android_apk.yml').read_text(encoding='utf-8')
    assert "['mobile_ui_version']" in wf
    assert 'steps.version.outputs.version' in wf
    assert "- 'config.json'" in wf
