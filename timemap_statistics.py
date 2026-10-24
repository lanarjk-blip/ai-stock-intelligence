"""Clock-slot evidence and chronological out-of-sample validation (no training)."""
import numpy as np
import pandas as pd
import market_session
from timemap_data import clean


def observations(frame, ticker, cutoff, cfg=None):
    """Each 30m candle predicts open->close; only completed previous days train.

    Provider timestamps are bar START times. Treating their closing price as known
    at the timestamp would leak up to 30 minutes into the past.
    """
    cutoff = pd.Timestamp(cutoff)
    d = clean(frame,ticker,cutoff)
    d = d[d.ts >= cutoff.tz_convert('UTC') - pd.Timedelta(days=59)]
    if d.empty:
        return pd.DataFrame(columns=['day','slot','ret'])
    local = d.ts.dt.tz_convert('Asia/Jakarta')
    d['day']=local.dt.date
    d['slot']=local.dt.strftime('%H:%M')
    regular = local.map(lambda ts: market_session.session_status(cfg or {},now=ts)['live'])
    d=d[regular & (d.day < cutoff.tz_convert('Asia/Jakarta').date())].copy()
    d['ret']=d.close/d.open-1
    return d[['day','slot','ret']].drop_duplicates(['day','slot']).sort_values(['day','slot'])


def walk_forward(obs, threshold=.002, min_train=8):
    """Fixed model/threshold. Predict each held-out day using earlier days only.

    Uses Laplace smoothing and reports multiclass Brier vs uniform, without
    fitting/tuning against held-out outcomes. No claim of validated skill at n<30.
    """
    predictions=[]
    for slot, group in obs.groupby('slot'):
        group=group.sort_values('day')
        labels=np.where(group.ret.to_numpy()>threshold,2,np.where(group.ret.to_numpy() < -threshold,0,1))
        counts=np.zeros(3)
        for i,label in enumerate(labels):
            if i>=min_train:
                p=(counts+1)/(counts.sum()+3)
                target=np.eye(3)[label]
                predictions.append({'day':str(group.iloc[i].day),'slot':slot,'train_end':str(group.iloc[i-1].day),
                                    'actual':int(label),'predicted':int(np.argmax(p)),
                                    'brier':float(np.sum((p-target)**2))})
            counts[label]+=1
    ev=pd.DataFrame(predictions)
    if ev.empty:
        return {'n':0,'accuracy_pct':None,'brier':None,'uniform_brier':2/3,'status':'INSUFFICIENT'},ev
    score=float(ev.brier.mean())
    recalls=[float((ev.loc[ev.actual==label,'predicted']==label).mean()) for label in range(3) if (ev.actual==label).any()]
    precision={str(label): float((ev.loc[ev.predicted==label,'actual']==label).mean()) if (ev.predicted==label).any() else None for label in range(3)}
    return {'n':len(ev),'accuracy_pct':float((ev.actual==ev.predicted).mean()*100),
            'balanced_accuracy_pct':float(np.mean(recalls)*100),
            'precision_down':precision['0'],'precision_up':precision['2'],
            'brier':score,'uniform_brier':2/3,
            'status':'INSUFFICIENT' if len(ev)<30 else 'ABOVE UNIFORM' if score < 2/3 else 'NO EDGE VS UNIFORM'},ev


def build(ticker, frame, now, cfg=None):
    now=pd.Timestamp(now).tz_convert('Asia/Jakarta'); cfg=cfg or {}
    obs=observations(frame,ticker,now,cfg)
    schedule_date=market_session.next_trading_date(cfg,now=now,after_close=False)
    schedule=pd.Timestamp(schedule_date,tz='Asia/Jakarta')
    threshold=float(cfg.get('time_return_threshold',.002))
    summary,_=walk_forward(obs,threshold)
    labels=['09:00','09:30','10:00','10:30','11:00','11:30','12:00','13:30','14:00','14:30','15:00','15:30','15:50','16:00']
    rows=[]
    for label in labels:
        h,m=map(int,label.split(':'))
        slot=schedule.replace(hour=h,minute=m,second=0,microsecond=0)
        ss=market_session.session_status(cfg,now=slot)
        session={'SESSION_1':'Sesi I','SESSION_2':'Sesi II'}.get(ss['status'],ss['status'])
        if label in ('15:50','16:00') and market_session.is_trading_date(schedule.date(),cfg):
            session='PRE-CLOSING' if label=='15:50' else 'CLOSING'
        z=obs[obs.slot==label].ret
        row=dict(time=label,session=session,horizon='30m' if ss['live'] else 'MARKET',samples=len(z) if ss['live'] else None,
                 up_pct=None,sideways_pct=None,down_pct=None,mean_return_pct=None,confidence_pct=None,
                 status='LIMITED' if ss['live'] else 'CHECKPOINT',note='Sampel historis belum cukup.',
                 ticker=ticker,trusted=False,schedule_date=schedule_date,historical_asof=str(obs.day.max()) if not obs.empty else '',
                 validation_n=summary['n'],validation_accuracy_pct=summary['accuracy_pct'],validation_brier=summary['brier'],validation_status=summary['status'])
        if ss['live'] and len(z)>=8:
            counts=np.array([(z < -threshold).sum(),(z.abs()<=threshold).sum(),(z>threshold).sum()])
            p=(counts+1)/(len(z)+3)
            row.update(down_pct=p[0]*100,sideways_pct=p[1]*100,up_pct=p[2]*100,mean_return_pct=float(z.mean()*100),
                       status='HISTORICAL ONLY',note='Frekuensi historis open→close 30m; bukan prediksi live. Confidence tidak diklaim sebagai akurasi.')
        rows.append(row)
    out=pd.DataFrame(rows)
    out.attrs.update(frame.attrs)
    out.attrs['validation']=summary
    out.attrs['calculated_at']=now.isoformat()
    out.attrs['model_version']='R90-slot-laplace'
    out.attrs['forecast_generated_at']=None
    return out
