"""Post-process the validated R23 patch so repeated CI runs stay idempotent."""
from pathlib import Path

# The V2 patch performs all substantive migrations. Two replacements in V2 have
# old blocks that are prefixes of their new blocks, so repeated CI runs can add
# duplicate lines. Always normalize immediately afterwards.
import apply_r23_actionability_patch_v2  # noqa: F401

p=Path('dynamic_engine.py')
s=p.read_text(encoding='utf-8')

# Collapse repeated imports created by prior audit runs.
while 'import app\nimport market_session\nimport market_session\n' in s:
    s=s.replace('import app\nimport market_session\nimport market_session\n','import app\nimport market_session\n',1)

# Collapse repeated monitor-only guards created by prior audit runs.
guard=(
    "    mode=str(getattr(row,'candidate_mode','') or '').upper()\n"
    "    if mode=='MONITOR_FALLBACK': return 'MONITOR'\n"
)
while guard+guard in s:
    s=s.replace(guard+guard,guard,1)

# Normalize decision names and block every sell/avoid alias from actionable state.
old=(
    "def _state(row, ts, score):\n"
    "    decision=str(row.decision)\n"
    "    es=str(ts.get('entry_status','NEUTRAL'))\n"
    + guard +
    "    if es=='ACTIVE' and score>=65 and decision not in ('SELL/AVOID',): return 'ENTRY READY'\n"
    "    if es=='DEVELOPING' and score>=60 and decision not in ('SELL/AVOID',): return 'DEVELOPING'\n"
    "    if decision in ('BUY','NEAR ENTRY'): return 'DEVELOPING'\n"
    "    if decision=='WATCH': return 'WATCH'\n"
    "    return 'MONITOR'\n"
)
new=(
    "def _state(row, ts, score):\n"
    "    decision=str(row.decision or '').upper().strip()\n"
    "    es=str(ts.get('entry_status','NEUTRAL')).upper().strip()\n"
    + guard +
    "    blocked={'SELL','SELL/AVOID','AVOID','STRONG_SELL','EXIT'}\n"
    "    if es=='ACTIVE' and score>=65 and decision not in blocked: return 'ENTRY READY'\n"
    "    if es=='DEVELOPING' and score>=60 and decision not in blocked: return 'DEVELOPING'\n"
    "    if decision in ('BUY','STRONG_BUY','NEAR ENTRY'): return 'DEVELOPING'\n"
    "    if decision=='WATCH': return 'WATCH'\n"
    "    return 'MONITOR'\n"
)
if old in s:
    s=s.replace(old,new,1)
elif new not in s:
    raise SystemExit('normalized _state block not found')

p.write_text(s,encoding='utf-8')
print('R23 ACTIONABILITY PATCH V3 NORMALIZED')
