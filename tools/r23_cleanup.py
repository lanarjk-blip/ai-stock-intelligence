from pathlib import Path

p = Path('recommendation_history.py')
s = p.read_text(encoding='utf-8')

block = '''    existing = {str(r[1]) for r in con.execute("PRAGMA table_info(recommendation_history)").fetchall()}\n    if "candidate_mode" not in existing:\n        con.execute("ALTER TABLE recommendation_history ADD COLUMN candidate_mode TEXT")\n'''
count = s.count(block)
if count < 1:
    raise SystemExit('candidate_mode migration block missing')
if count > 1:
    first = s.index(block)
    before = s[:first + len(block)]
    after = s[first + len(block):].replace(block, '')
    s = before + after

if s.count(block) != 1:
    raise SystemExit(f'candidate_mode migration cleanup failed: count={s.count(block)}')

p.write_text(s, encoding='utf-8')
print('R23 CLEANUP PASSED: candidate_mode migration exactly once')
