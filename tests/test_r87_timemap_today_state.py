import pandas as pd
import timemap_ui_direct_live as tm


class _St:
    session_state={"_timemap_active_ticker":"MPXL"}
    query_params={}


def _base():
    return pd.DataFrame([
        {"Waktu":"09:00","Sesi":"Sesi I","Samples":10,"UP %":60.0,"Sideways %":10.0,"DOWN %":30.0},
        {"Waktu":"09:30","Sesi":"Sesi I","Samples":10,"UP %":55.0,"Sideways %":15.0,"DOWN %":30.0},
        {"Waktu":"10:00","Sesi":"Sesi I","Samples":10,"UP %":52.0,"Sideways %":18.0,"DOWN %":30.0},
    ])


def _tape(date, closes):
    ts=pd.to_datetime([f"{date} 02:00:00Z",f"{date} 02:30:00Z",f"{date} 03:00:00Z"])
    d=pd.DataFrame({
        "ts":ts,
        "open":[100.0,closes[0],closes[1]],
        "high":[max(100.0,closes[0]),max(closes[:2]),max(closes)],
        "low":[min(100.0,closes[0]),min(closes[:2]),min(closes)],
        "close":closes,
        "volume":[1000.0,1400.0,1800.0],
    })
    d["wib"]=d.ts.dt.tz_convert("Asia/Jakarta")
    d["minute"]=d.wib.dt.hour*60+d.wib.dt.minute
    return {
        "ticker":"MPXL","market_date":date,"market_status":"AFTER_MARKET",
        "live_required":False,"fresh":True,"age_min":0.0,"reason":"SESSION PAUSE",
        "source":"DIRECT PROVIDER","frame":d,
    }


def test_two_market_days_produce_different_realized_checkpoint_map(monkeypatch):
    monkeypatch.setattr(tm,"_today_tape",lambda _ticker:_tape("2026-10-22",[101.0,103.0,104.0]))
    day1=tm._augment(_base(),_St())
    monkeypatch.setattr(tm,"_today_tape",lambda _ticker:_tape("2026-10-23",[99.0,97.0,96.0]))
    day2=tm._augment(_base(),_St())
    assert list(day1["Live Price"]) != list(day2["Live Price"])
    assert set(day1["Market Date"])=={"2026-10-22"}
    assert set(day2["Market Date"])=={"2026-10-23"}
    assert day1.iloc[-1]["Live Direction"]=="UP"
    assert day2.iloc[-1]["Live Direction"]=="DOWN"
    assert (day2["Checkpoint State"]=="REALIZED TODAY").all()


def test_current_day_is_a_hard_boundary_in_source():
    text=open(tm.__file__,encoding="utf-8").read()
    assert 'd = d[d["wib"].dt.date.astype(str) == market_date].copy()' in text
    assert 'NO BAR FOR ACTIVE MARKET DAY' in text
    assert 'Previous-session' in text


def test_today_state_is_shown_before_historical_baseline():
    out=tm._order(_base().assign(**{"Live Price":100.0,"Live Direction":"SIDEWAYS","Checkpoint State":"REALIZED TODAY"}))
    cols=list(out.columns)
    assert cols.index("Checkpoint State") < cols.index("Samples")
    assert cols.index("Live Price") < cols.index("UP %")
    assert cols.index("Live Direction") < cols.index("DOWN %")
