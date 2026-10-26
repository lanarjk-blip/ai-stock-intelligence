import pandas as pd
import pytest

from trading_monitor import build_monitor, date_context
from market_session import session_status


def tape(ticker='PTSP', stamp='2026-10-26T14:00:00+07:00', close=2100, volume=500):
    d = pd.DataFrame([dict(ts=stamp, open=2250, high=2300, low=2000, close=close, volume=volume)])
    d.attrs.update(ticker=ticker, source='TEST FIXTURE')
    return d


def frame(day='2026-10-26', up=60):
    return pd.DataFrame([
        {'Data Type':'TODAY CHECKPOINT', 'Waktu':'14:00', 'Live Ticker':'PTSP',
         'Market Date':day, 'Checkpoint State':'REALIZED TODAY', 'Checkpoint Bar':'14:00',
         'Hasil Tersedia WIB':day+'T14:30:00+07:00', 'Slot Return %':-6.67,
         'Live Direction':'UP', 'Baseline UP %':up, 'Baseline Sideways %':20, 'Baseline DOWN %':20},
        {'Data Type':'TODAY CHECKPOINT', 'Waktu':'15:00', 'Live Ticker':'PTSP',
         'Market Date':day, 'Checkpoint State':'WAIT FUTURE', 'Baseline UP %':55,
         'Baseline Sideways %':20, 'Baseline DOWN %':25},
    ])


def make(data=None, now='2026-10-26T14:33:00+07:00', f=None, opportunities=None):
    return build_monitor(frame() if f is None else f, tape() if data is None else data,
                         'PTSP', session_status(now=pd.Timestamp(now)), opportunities)


def test_ptsp_historical_up_cannot_override_actual_drop_or_forecast_future():
    v = make()
    row = v['schedule'].iloc[0]
    assert row['Realisasi hari ini'] == 'DOWN -6.67%'
    assert row['UP historis %'] == '60.00'
    assert v['status'] == 'WAIT'  # old candle, despite historical UP
    future = v['schedule'].iloc[1]
    assert future['Realisasi hari ini'] == '—'
    assert future['Status interval'] == 'WAIT • NO EDGE'


@pytest.mark.parametrize('now,bar', [
    ('2026-10-26T10:00:00+07:00','2026-10-23T15:00:00+07:00'),
    ('2026-10-27T10:00:00+07:00','2026-10-26T15:00:00+07:00'),
    ('2026-11-02T10:00:00+07:00','2026-10-30T15:00:00+07:00'),
    ('2027-01-04T10:00:00+07:00','2026-12-30T15:00:00+07:00'),
    ('2026-10-26T14:00:00+07:00','2026-10-26T11:30:00+07:00'),
])
def test_rollovers_never_promote_previous_day_or_session(now,bar):
    v = make(tape(stamp=bar),now)
    assert v['status'] == 'WAIT' and v['direction'] == 'WAIT'


def test_provider_failure_and_cross_ticker_fail_closed():
    d=tape(stamp='2026-10-26T14:30:00+07:00')
    d.attrs['fallback']=True
    assert make(d)['status']=='WAIT'
    v=make(tape('MPXL'))
    assert v['price'] is None and v['status']=='WAIT'
    assert make(pd.DataFrame())['data_status']=='DATA UNAVAILABLE'


def test_fresh_bar_describes_movement_without_buy_or_probability():
    v=make(tape(stamp='2026-10-26T14:30:00+07:00'))
    assert v['direction']=='DOWN'
    assert v['status']=='WAIT'
    assert v['movement_pct']==pytest.approx(-6.6666667)
    assert 'confidence' not in v


@pytest.mark.parametrize('volume', [0, None])
def test_illiquid_ticker_has_no_direction(volume):
    v=make(tape(stamp='2026-10-26T14:30:00+07:00',volume=volume))
    assert v['direction']=='WAIT'


def test_no_relabeling_old_or_unfinished_realization():
    assert make(f=frame('2026-10-23'))['schedule'].iloc[0]['Realisasi hari ini']=='—'
    assert make(now='2026-10-26T14:02:00+07:00')['schedule'].iloc[0]['Realisasi hari ini']=='—'
    f=frame(); f.loc[0,'Baseline UP %']=99
    assert make(f=f)['schedule'].iloc[0]['UP historis %']=='—'


@pytest.mark.parametrize('volume',[0,None])
def test_realized_label_requires_actual_trading_volume(volume):
    assert make(tape(volume=volume))['schedule'].iloc[0]['Realisasi hari ini']=='—'


def test_partial_session_and_missing_bar_never_become_realized():
    f=frame(); f.loc[0,'Waktu']='15:30'; f.loc[0,'Checkpoint Bar']='15:30'
    f.loc[0,'Hasil Tersedia WIB']='2026-10-26T16:00:00+07:00'
    v=make(tape(stamp='2026-10-26T15:30:00+07:00'),now='2026-10-26T16:30:00+07:00',f=f)
    assert v['schedule'].iloc[0]['Realisasi hari ini']=='—'
    assert make(tape(stamp='2026-10-26T14:30:00+07:00'))['schedule'].iloc[0]['Realisasi hari ini']=='—'


def test_dynamic_other_ticker_old_session_and_fresh_snapshot():
    d=tape(stamp='2026-10-26T14:30:00+07:00')
    def opp(ticker,when):
        return pd.DataFrame([dict(ticker=ticker,generated_at=when,state='ENTRY READY',entry_status='ACTIVE')])
    for ticker,when in [('MPXL','2026-10-26T14:32:00+07:00'),
                         ('PTSP','2026-10-26T10:00:00+07:00'),
                         ('PTSP','2026-10-26T14:34:00+07:00'),
                         ('PTSP','2026-10-26 14:32:00')]:
        assert not make(d,opportunities=opp(ticker,when))['dynamic']['usable']
    v=make(d,opportunities=opp('PTSP','2026-10-26T14:32:00+07:00'))
    assert v['dynamic']['usable'] and v['status']=='WAIT'


def test_future_tape_is_not_visible_and_input_preserved():
    d=pd.concat([tape(),tape(stamp='2026-10-26T15:00:00+07:00',close=2300)],ignore_index=True)
    d.attrs['ticker']='PTSP'
    original=d.copy(deep=True)
    assert make(d)['price']==2100
    pd.testing.assert_frame_equal(d,original)


def test_market_closed_is_archive_even_when_bar_is_recent():
    v=make(tape(stamp='2026-10-26T15:30:00+07:00'),now='2026-10-26T16:30:00+07:00')
    assert v['status']=='WAIT' and v['data_status']=='HISTORICAL ONLY'


@pytest.mark.parametrize('ticker', ['PTSP','MPXL','BBCA','ANTM','ZZZZ'])
def test_shared_monitor_supports_any_valid_ticker(ticker):
    f=frame(); f['Live Ticker']=ticker
    v=build_monitor(f,tape(ticker=ticker),' '+ticker+'.JK',session_status(now=pd.Timestamp('2026-10-26T14:33:00+07:00')))
    assert v['ticker']==ticker and len(v['schedule'])==2
    assert v['schedule'].iloc[0]['Realisasi hari ini']=='DOWN -6.67%'


def test_renderer_shows_compact_full_day_and_preserves_details(monkeypatch):
    import timemap_ui_direct_live as layer
    import timemap_data
    from streamlit.testing.v1 import AppTest
    now=pd.Timestamp('2026-10-26T14:33:00+07:00')
    session=session_status(now=now)
    d=tape()
    monkeypatch.setattr(timemap_data,'load',lambda *args,**kwargs: d.copy())
    monkeypatch.setattr(layer,'_provider_frame',lambda ticker: d.copy())
    monkeypatch.setattr(layer,'_session',lambda: (session['status'],session['live'],session['market_date'],session['wib']))
    app=AppTest.from_string('''
import pandas as pd
from trading_monitor import render_monitor
from market_session import session_status
history=pd.DataFrame({'Waktu':['14:00','15:00'],'Sesi':['Sesi II','Sesi II'],'UP %':[60.,55.],'Sideways %':[20.,20.],'DOWN %':[20.,25.],'Samples':[30,30]})
history.attrs['ticker']='PTSP'
render_monitor(history,'PTSP',session_status(now=pd.Timestamp('2026-10-26T14:33:00+07:00')),{},pd.DataFrame())
''').run(timeout=20)
    assert not app.exception
    compact=app.dataframe[0].value
    assert compact['Waktu WIB'].tolist()==['14:00','15:00']
    assert len(compact.columns)==6
    assert compact.iloc[0]['Realisasi 26-10-2026'].startswith('DOWN')
    assert compact.iloc[1]['Status interval']=='WAIT • NO EDGE'
    assert len(app.dataframe)==3
    assert not app.expander[0].proto.expanded
    assert any('DATA HARGA: Senin, 26 Oktober 2026 — HARI INI' in m.value for m in app.markdown)
    assert any('ACUAN JADWAL HISTORIS: BELUM TERVERIFIKASI' in m.value for m in app.markdown)


@pytest.mark.parametrize('now,target,relation',[
    ('2026-10-26T10:00:00+07:00','2026-10-26','HARI INI'),
    ('2026-10-26T22:00:00+07:00','2026-10-27','BESOK'),
    ('2026-10-23T22:00:00+07:00','2026-10-26','HARI PERDAGANGAN BERIKUTNYA'),
    ('2026-12-31T22:00:00+07:00','2027-01-04','HARI PERDAGANGAN BERIKUTNYA'),
    ('2026-10-27T10:00:00+07:00','2026-10-26','JADWAL LAMA'),
])
def test_schedule_date_is_explicit_not_inferred_from_refresh(now,target,relation):
    f=frame(); f['schedule_date']=target; f['historical_asof']='2026-10-23'
    checked=pd.Timestamp(now)
    context=date_context(f,checked,pd.Timestamp('2026-10-23T15:00:00+07:00'))
    assert context['schedule_date']==target
    assert context['schedule_relation']==relation
    assert context['data_label']=='Jumat, 23 Oktober 2026'
    expected = 'HARI INI' if checked.date() == pd.Timestamp('2026-10-23').date() else 'DATA LAMA / HISTORIS'
    assert context['data_relation'] == expected
    assert context['historical_label']=='Jumat, 23 Oktober 2026'


@pytest.mark.parametrize('dates', [[],[''],['not a date'],['2026-10-26','2026-10-27']])
def test_missing_or_conflicting_schedule_does_not_invent_tomorrow(dates):
    context=date_context(pd.DataFrame({'schedule_date':dates}),pd.Timestamp('2026-10-26T22:00:00+07:00'),None)
    assert context['schedule_date'] is None
    assert context['schedule_label']=='BELUM TERVERIFIKASI'
    assert context['data_relation']=='BELUM ADA DATA'
