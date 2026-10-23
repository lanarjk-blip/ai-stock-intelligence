import pandas as pd
import timemap_ui_direct_live as tm


class DummySt:
    session_state = {}
    query_params = {}


def history_frame():
    return pd.DataFrame([
        {
            'Waktu': '09:00', 'Sesi': 'Sesi I', 'Horizon': '30m', 'Samples': 20,
            'UP %': 60.0, 'Sideways %': 10.0, 'DOWN %': 30.0,
            'Mean Return %': 0.7, 'Confidence %': 58.0, 'Status': 'ACTIVE',
            'Catatan': 'estimasi historis slot.'
        },
        {
            'Waktu': '09:30', 'Sesi': 'Sesi I', 'Horizon': '30m', 'Samples': 18,
            'UP %': 33.3, 'Sideways %': 22.2, 'DOWN %': 44.5,
            'Mean Return %': -0.3, 'Confidence %': 52.0, 'Status': 'WAIT',
            'Catatan': 'estimasi historis slot.'
        },
    ])


def live_payload(**overrides):
    data = {
        'ticker': 'ZZZZ', 'market_date': '2026-10-23', 'market_status': 'SESSION_1',
        'live_required': True, 'fresh': True, 'bars': 6,
        'open': 100.0, 'high': 103.0, 'low': 96.0, 'price': 97.0,
        'return_pct': -3.0, 'momentum_pct': -0.8, 'volume_ratio': 1.4,
        'range_position_pct': 14.3, 'price_vs_vwap_pct': -1.1,
        'age_min': 4.0, 'direction': 'DOWN', 'confidence_pct': 87.0,
        'reason': 'FRESH', 'source': 'UI_DIRECT',
    }
    data.update(overrides)
    return data


def test_r87_today_live_is_a_separate_first_row(monkeypatch):
    monkeypatch.setattr(tm, '_active_ticker', lambda st: 'ZZZZ')
    monkeypatch.setattr(tm, '_live', lambda ticker: live_payload(ticker=ticker))
    monkeypatch.setattr(tm, '_matched_historical_row', lambda frame: frame.index[0])
    out = tm._augment(history_frame(), DummySt())

    assert out.iloc[0]['Data Type'] == 'TODAY LIVE'
    assert out.iloc[0]['Market Date'] == '2026-10-23'
    assert out.iloc[0]['Live Price'] == 97.0
    assert out.iloc[0]['Live Return %'] == -3.0
    assert out.iloc[0]['Live Direction'] == 'DOWN'
    assert out.iloc[0]['UP %'] is None or pd.isna(out.iloc[0]['UP %'])
    assert out.iloc[0]['Baseline UP %'] == 60.0
    assert (out.iloc[1:]['Data Type'] == 'HISTORICAL BASELINE').all()


def test_r87_missing_today_data_never_reuses_history_as_live(monkeypatch):
    monkeypatch.setattr(tm, '_active_ticker', lambda st: 'ZZZZ')
    monkeypatch.setattr(
        tm,
        '_live',
        lambda ticker: live_payload(
            ticker=ticker, fresh=False, bars=0, open=None, high=None, low=None,
            price=None, return_pct=None, momentum_pct=None, direction='WAIT',
            reason='NO BAR FOR ACTIVE MARKET DAY'
        ),
    )
    monkeypatch.setattr(tm, '_matched_historical_row', lambda frame: frame.index[0])
    out = tm._augment(history_frame(), DummySt())

    assert out.iloc[0]['Market Date'] == '2026-10-23'
    assert pd.isna(out.iloc[0]['Live Price'])
    assert out.iloc[0]['Status'] == 'WAIT'
    assert out.iloc[0]['Live Confirmation'] == 'WAIT • DATA NOT FRESH'
    assert out.iloc[0]['Live Reason'] == 'NO BAR FOR ACTIVE MARKET DAY'
    assert out.iloc[1]['UP %'] == 60.0


def test_r87_source_has_no_symbol_specific_logic():
    source = open(tm.__file__, encoding='utf-8').read()
    for symbol in ('MPXL', 'BBCA', 'BBRI', 'ANTM', 'PTBA'):
        assert symbol not in source
    assert 'if len(df)<60' not in source
    assert 'TODAY LIVE' in source
    assert 'HISTORICAL BASELINE' in source
