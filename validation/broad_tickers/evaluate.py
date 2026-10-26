"""Frozen-model external ticker diagnostic with explicitly hypothetical fills."""
import hashlib
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))
from validation.engine_upgrade.frozen import infer
from validation.engine_upgrade.train import baselines
from validation.quality.conditional_models import features,metrics
from timemap_data import clean

HERE=Path(__file__).parent


def validate_candidate(protocol, collection):
    path=Path(protocol['candidate_manifest'])
    manifest=json.loads(path.read_text())
    checksum=hashlib.sha256(path.read_bytes()).hexdigest()
    if checksum != collection['candidate_sha256'] or manifest['training_end'] != protocol['candidate_training_end']:
        raise ValueError('Frozen candidate changed after collection protocol')
    dates=protocol['evaluation_dates']
    if not dates or len(dates)!=len(set(dates)) or min(dates)<=manifest['training_end']:
        raise ValueError('Evaluation must follow frozen training boundary')
    return checksum


def select_trades(rows, prefix, policy):
    """Signal and ranking never reference realized returns or target OHLC."""
    gate=(rows[prefix+'_up']>=policy['p_up_min'])
    gate &= rows[prefix+'_up']-rows[prefix+'_down']>=policy['p_up_minus_p_down_min']
    gate &= rows.volume_ratio>=policy['prior_volume_ratio_min']
    gate &= rows.gap_hours.between(0,policy['gap_hours_max'])
    selected=rows.loc[gate].copy()
    selected['_edge']=selected[prefix+'_up']-selected[prefix+'_down']
    return selected.sort_values(['ts','_edge','ticker'],ascending=[True,False,True]).drop_duplicates('ts').drop(columns='_edge')


def paper_metrics(trades, fee_pct, slippage_pct):
    """Ideal open/close fills. These results cannot establish executable profit."""
    if trades.empty:
        return dict(trades=0,win_rate=None,mean_net_return_pct=None,profit_factor=None,day_block_mean_net95=None)
    net=trades.actual_return_pct.to_numpy()-fee_pct-slippage_pct
    gains=net[net>0].sum();losses=-net[net<0].sum()
    daily=pd.DataFrame({'day':trades.day.to_numpy(),'net':net}).groupby('day').net.mean().to_numpy()
    bootstrap=np.random.default_rng(91).choice(daily,size=(2000,len(daily)),replace=True).mean(axis=1)
    return dict(trades=len(net),evaluation_days=len(daily),win_rate=float((net>0).mean()),
                mean_gross_return_pct=float(trades.actual_return_pct.mean()),mean_net_return_pct=float(net.mean()),
                profit_factor=float(gains/losses) if losses>0 else None,
                day_block_mean_net95=np.quantile(bootstrap,[.025,.975]).tolist(),
                fee_round_trip_pct=fee_pct,slippage_round_trip_pct=slippage_pct,
                actual_execution=False)


def run():
    protocol=json.loads((HERE/'protocol.json').read_text())
    collection=json.loads((HERE/'collection.json').read_text())
    candidate_checksum=validate_candidate(protocol,collection)
    cutoff=pd.Timestamp(collection['checked_at'])
    eligible=[];coverage=[];inputs={}
    for ticker in protocol['tickers']:
        paths=[HERE/'inputs'/f'{ticker}.csv',HERE/'recovered'/f'{ticker}.csv']
        paths=[p for p in paths if p.exists()]
        if not paths:
            coverage.append(dict(ticker=ticker,status='NO DATA',history_days=0,test_n=0));continue
        inputs[ticker]={str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}
        raw=clean(pd.concat([pd.read_csv(p) for p in paths],ignore_index=True),ticker,cutoff)
        d=features(raw,ticker,cutoff)
        prior=d[d.day.astype(str)<=protocol['candidate_training_end']]
        test=d[d.day.astype(str).isin(protocol['evaluation_dates'])].copy()
        enough=(d.day.nunique()>=protocol['minimum_history_days'] and prior.day.nunique()>=protocol['minimum_prior_training_days'] and len(test)>=protocol['minimum_test_outcomes_per_ticker'])
        coverage.append(dict(ticker=ticker,status='EVALUATED' if enough else 'INSUFFICIENT HISTORY',history_days=d.day.nunique(),prior_days=prior.day.nunique(),test_n=len(test)))
        if not enough:continue
        test=test.merge(raw[['ts','open','close']],on='ts',how='left',validate='one_to_one')
        test['actual_return_pct']=(test.close/test.open-1)*100
        p=infer(test)
        baseline=baselines(prior,test)['slot_baseline']
        for prefix,probs in [('candidate',p),('slot',baseline)]:
            test[prefix+'_down']=probs[:,0];test[prefix+'_flat']=probs[:,1];test[prefix+'_up']=probs[:,2]
        eligible.append(test)
    report=dict(checked_at=pd.Timestamp.now(tz='Asia/Jakarta').isoformat(),production_enabled=False,
                candidate_sha256=candidate_checksum,evaluation_dates=protocol['evaluation_dates'],
                protocol_sha256=hashlib.sha256((HERE/'protocol.json').read_bytes()).hexdigest(),input_sha256=inputs,
                coverage=coverage,requested=len(protocol['tickers']),evaluated_tickers=[d.ticker.iloc[0] for d in eligible],
                decision='NO PROMOTION: retrospective ticker extension, idealized fills and incomplete coverage',
                assumptions=protocol['paper_trade'],costs_are_assumptions=True)
    if eligible:
        data=pd.concat(eligible,ignore_index=True).sort_values(['ts','ticker'])
        report['directional_metrics']={}
        report['per_ticker']={}
        for name in ['candidate','slot']:
            p=data[[name+'_down',name+'_flat',name+'_up']].to_numpy()
            report['directional_metrics'][name]=metrics(data.label,p)
        for ticker,z in data.groupby('ticker'):
            report['per_ticker'][ticker]={name:metrics(z.label,z[[name+'_down',name+'_flat',name+'_up']].to_numpy()) for name in ['candidate','slot']}
        report['paper_strategies']={}
        ledger=[]
        for name in ['candidate','slot']:
            trades=select_trades(data,name,protocol['fixed_long_signal'])
            report['paper_strategies'][name]={str(fee):paper_metrics(trades,fee,protocol['extra_slippage_round_trip_pct']) for fee in protocol['cost_scenarios_round_trip_pct']}
            for row in trades.itertuples():
                ledger.append(dict(method=name,ticker=row.ticker,candle_start=str(row.ts),feature_asof=str(row.feature_asof),
                                   actual_return_pct=row.actual_return_pct,retrospective=True,actual_execution=False))
        (HERE/'paper_trades.json').write_text(json.dumps(ledger,indent=2))
        data.to_csv(HERE/'evaluated_rows.csv',index=False)
    (HERE/'results.json').write_text(json.dumps(report,indent=2))
    print(json.dumps({k:v for k,v in report.items() if k in ['coverage','evaluated_tickers','paper_strategies','decision']},indent=2))


if __name__=='__main__':run()
