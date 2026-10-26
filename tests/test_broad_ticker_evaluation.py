import hashlib
import json
from pathlib import Path

import pandas as pd
import pytest

from validation.broad_tickers.evaluate import select_trades,paper_metrics,validate_candidate


POLICY=dict(p_up_min=.5,p_up_minus_p_down_min=.15,prior_volume_ratio_min=.8,gap_hours_max=.5)


def rows():
    return pd.DataFrame([
        dict(ticker='AMRT',ts='2026-10-14T03:00Z',day='2026-10-14',candidate_up=.6,candidate_down=.2,volume_ratio=1.,gap_hours=0.,actual_return_pct=-5.),
        dict(ticker='ACES',ts='2026-10-14T03:00Z',day='2026-10-14',candidate_up=.55,candidate_down=.3,volume_ratio=1.,gap_hours=0.,actual_return_pct=10.),
        dict(ticker='BMRI',ts='2026-10-14T03:30Z',day='2026-10-14',candidate_up=.6,candidate_down=.2,volume_ratio=1.,gap_hours=0.,actual_return_pct=.5),
    ])


def test_selection_cannot_pick_winners_using_future_returns():
    r=rows();before=select_trades(r,'candidate',POLICY)
    r['actual_return_pct']=[100.,-100.,-20.]
    after=select_trades(r,'candidate',POLICY)
    assert before[['ticker','ts']].equals(after[['ticker','ts']])
    assert before.ticker.tolist()==['AMRT','BMRI']
    assert not before.ts.duplicated().any()


def test_costs_can_turn_a_gross_win_into_a_loss():
    r=rows().iloc[[2]]
    low=paper_metrics(r,.3,.1);high=paper_metrics(r,.5,.1)
    assert low['mean_net_return_pct']==pytest.approx(.1)
    assert high['mean_net_return_pct']==pytest.approx(-.1)
    assert high['win_rate']==0 and not high['actual_execution']


def test_wait_and_no_trades_do_not_become_successful_recommendations():
    r=rows();r['candidate_up']=.4
    assert select_trades(r,'candidate',POLICY).empty
    assert paper_metrics(r.iloc[:0],.3,.1)['win_rate'] is None
    r=rows();r['gap_hours']=17.
    assert select_trades(r,'candidate',POLICY).empty
    r=rows();r['volume_ratio']=.1
    assert select_trades(r,'candidate',POLICY).empty


def test_ticker_ties_are_deterministic_and_inputs_remain_unchanged():
    r=rows();r.loc[1,['candidate_up','candidate_down']]=[.6,.2]
    snapshot=r.copy(deep=True)
    assert select_trades(r,'candidate',POLICY).ticker.iloc[0]=='ACES'
    pd.testing.assert_frame_equal(r,snapshot)


def test_candidate_and_test_boundary_cannot_silently_change(tmp_path):
    path=tmp_path/'manifest.json';path.write_text(json.dumps({'training_end':'2026-10-13'}))
    p=dict(candidate_manifest=str(path),candidate_training_end='2026-10-13',evaluation_dates=['2026-10-14'])
    c=dict(candidate_sha256=hashlib.sha256(path.read_bytes()).hexdigest())
    validate_candidate(p,c)
    p['evaluation_dates']=['2026-10-13']
    with pytest.raises(ValueError):validate_candidate(p,c)
    p['evaluation_dates']=['2026-10-14'];path.write_text('{}')
    with pytest.raises((ValueError,KeyError)):validate_candidate(p,c)


def test_archived_coverage_and_cost_records_cannot_become_live_recommendations():
    root=Path('validation/broad_tickers')
    report=json.loads((root/'results.json').read_text())
    protocol=json.loads((root/'protocol.json').read_text())
    trades=json.loads((root/'paper_trades.json').read_text())
    assert not report['production_enabled'] and report['decision'].startswith('NO PROMOTION')
    assert len(report['coverage'])==len(protocol['tickers'])==20
    evaluated={r['ticker'] for r in report['coverage'] if r['status']=='EVALUATED'}
    assert set(report['evaluated_tickers'])==evaluated
    assert all(t['ticker'] in evaluated and not t['actual_execution'] and t['retrospective'] for t in trades)
    assert all(pd.Timestamp(t['feature_asof'])<=pd.Timestamp(t['candle_start']) for t in trades)
    assert len({(t['method'],t['candle_start']) for t in trades})==len(trades)
    for paths in report['input_sha256'].values():
        for path,digest in paths.items():
            assert hashlib.sha256(Path(path).read_bytes()).hexdigest()==digest
    assert hashlib.sha256((root/'protocol.json').read_bytes()).hexdigest()==report['protocol_sha256']
