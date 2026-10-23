import pandas as pd
import timemap_checkpoint_today as cp
import timemap_ui_direct_live as base


class DummySt:
    session_state={}
    query_params={}


def history_frame():
    rows=[]
    for t,s in [
        ('09:00','Sesi I'),('09:30','Sesi I'),('10:00','Sesi I'),('10:30','Sesi I'),
        ('11:00','Sesi I'),('11:30','Sesi I'),('12:00','BREAK'),
        ('13:30','Sesi II'),('14:00','Sesi II'),('14:30','Sesi II'),('15:00','Sesi II'),('15:30','Sesi II')
    ]:
        rows.append({'Waktu':t,'Sesi':s,'Horizon':'30m','Samples':20,'UP %':50.0,'Sideways %':20.0,'DOWN %':30.0,'Mean Return %':0.1,'Confidence %':55.0,'Status':'NEUTRAL'})
    return pd.DataFrame(rows)


def test_live_same_market_day_checkpoint_acceptance():
    history=history_frame()
    for ticker in ('MPXL','BBCA'):
        class BoundBase:
            _session=staticmethod(base._session)
            _provider_frame=staticmethod(base._provider_frame)
            _cfg=staticmethod(base._cfg)
            _time_column=staticmethod(lambda frame:'Waktu')
            _active_ticker=staticmethod(lambda _st,t=ticker:t)

        state=cp._prepare_day(BoundBase,ticker)
        day=state['frame']
        print('ACCEPTANCE',ticker,'market_date=',state['market_date'],'status=',state['market_status'],'fresh=',state['fresh'],'reason=',state['reason'],'bars=',len(day))
        if not day.empty:
            print(day[['wib','open','high','low','close','volume']].tail(8).to_string(index=False))
        assert not day.empty, f'{ticker}: provider has no bars for active market day {state["market_date"]}: {state["reason"]}'
        assert day['wib'].dt.date.astype(str).eq(state['market_date']).all()

        out=cp._checkpoint_rows(history,DummySt(),BoundBase)
        realized=out[out['Checkpoint State']=='REALIZED TODAY']
        print(out[['Waktu','Checkpoint State','Checkpoint Bar','Live Price','Live Return %','Live Direction','Live Confirmation','Market Date']].to_string(index=False))
        assert not realized.empty, f'{ticker}: no realized current-day checkpoint'
        assert realized['Live Price'].notna().all()
        assert realized['Market Date'].eq(state['market_date']).all()
        assert realized['Live Direction'].isin(['UP','DOWN','SIDEWAYS']).all()
