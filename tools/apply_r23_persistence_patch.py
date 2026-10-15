from pathlib import Path

# Re-run from the current branch head so the validated persistence change can
# land after concurrent actionability/test commits.
p=Path('persistent_bridge.py')
s=p.read_text(encoding='utf-8')

repls=[
(
'''        ai_score REAL,
        risk TEXT,
        entry_low REAL,
''',
'''        ai_score REAL,
        risk TEXT,
        reference_price REAL,
        entry_low REAL,
'''
),
(
'''    con.execute("""CREATE TABLE IF NOT EXISTS premarket_runs (
        market_date TEXT PRIMARY KEY,
        generated_at TEXT NOT NULL,
        status TEXT NOT NULL,
        pick_count INTEGER NOT NULL,
        note TEXT
    )""")
''',
'''    con.execute("""CREATE TABLE IF NOT EXISTS premarket_runs (
        market_date TEXT PRIMARY KEY,
        generated_at TEXT NOT NULL,
        status TEXT NOT NULL,
        pick_count INTEGER NOT NULL,
        note TEXT
    )""")
    existing = {str(r[1]) for r in con.execute("PRAGMA table_info(premarket_picks)").fetchall()}
    if "reference_price" not in existing:
        con.execute("ALTER TABLE premarket_picks ADD COLUMN reference_price REAL")
'''
),
(
'''                "decision", "confidence", "ai_score", "risk", "entry_low",
                "entry_high", "stop_loss", "target_1", "target_2",
''',
'''                "decision", "confidence", "ai_score", "risk", "reference_price",
                "entry_low", "entry_high", "stop_loss", "target_1", "target_2",
'''
),
(
'''                   (market_date,generated_at,rank,ticker,company,decision,confidence,ai_score,risk,
                    entry_low,entry_high,stop_loss,target_1,target_2,ihsg_regime,reason,source_phase)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
''',
'''                   (market_date,generated_at,rank,ticker,company,decision,confidence,ai_score,risk,
                    reference_price,entry_low,entry_high,stop_loss,target_1,target_2,ihsg_regime,reason,source_phase)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
'''
),
]
for old,new in repls:
    if old in s:
        s=s.replace(old,new,1)
    elif new not in s:
        raise SystemExit('persistent bridge anchor missing: '+old.splitlines()[0])
p.write_text(s,encoding='utf-8')
print('R23 PERSISTENCE PATCH APPLIED')
