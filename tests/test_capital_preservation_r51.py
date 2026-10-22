import sqlite3

import capital_preservation as cp


def test_small_sample_is_fail_open():
    m={"samples":4,"loss_streak":4,"recent_avg":-2.0,"max_drawdown":-20.0,"ready":False}
    assert cp.capital_adjustment(m)==0.0
    assert cp.capital_state("ENTRY READY",m)=="ENTRY READY"


def test_three_loss_streak_downgrades_entry_ready():
    m={"samples":10,"loss_streak":3,"recent_avg":-0.4,"max_drawdown":-4.0,"ready":True}
    assert cp.capital_adjustment(m)==-6.0
    assert cp.capital_state("ENTRY READY",m)=="DEVELOPING"


def test_severe_drawdown_blocks_entry_ready():
    m={"samples":12,"loss_streak":1,"recent_avg":-0.6,"max_drawdown":-13.0,"ready":True}
    assert cp.capital_adjustment(m)==-11.0
    assert cp.capital_state("ENTRY READY",m)=="MONITOR"


def test_non_entry_state_is_not_promoted():
    m={"samples":12,"loss_streak":5,"recent_avg":-1.0,"max_drawdown":-15.0,"ready":True}
    assert cp.capital_state("MONITOR",m)=="MONITOR"
    assert cp.capital_state("DEVELOPING",m)=="DEVELOPING"


def test_recent_returns_uses_resolved_shadow_positions(tmp_path,monkeypatch):
    db=tmp_path/'market.db'
    con=sqlite3.connect(db)
    con.execute('CREATE TABLE shadow_positions(id INTEGER PRIMARY KEY,status TEXT,return_pct REAL)')
    rows=[
        (1,'TP2',3.0),(2,'STOP',-2.0),(3,'EXPIRED',1.0),(4,'STOP',-1.0),
        (5,'TP2',2.0),(6,'STOP',-2.0),(7,'STOP',-1.5),(8,'STOP',-1.0),
    ]
    con.executemany('INSERT INTO shadow_positions VALUES(?,?,?)',rows)
    con.commit(); con.close()
    monkeypatch.setattr(cp,'_dbcon',lambda: sqlite3.connect(db))
    monkeypatch.setattr(cp,'_cfg',lambda:{'capital_guard_window':20,'capital_guard_min_samples':8,'capital_guard_cache_seconds':60})
    cp._CACHE.update({'at':0.0,'metrics':None})
    m=cp._recent_returns(force=True)
    assert m['samples']==8
    assert m['loss_streak']==3
    assert m['ready'] is True
    assert m['recent_avg'] < 0
