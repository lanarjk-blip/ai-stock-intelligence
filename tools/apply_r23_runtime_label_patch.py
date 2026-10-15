# final-r23-runtime-label-trigger-v3
from pathlib import Path

p=Path('app.py')
s=p.read_text(encoding='utf-8')

replacements={
    "st.set_page_config(page_title='AI Stock Intelligence V1.8.48', page_icon='📈', layout='wide')":
        "st.set_page_config(page_title=f\"AI Stock Intelligence V{CFGD.get('mobile_ui_version','1.8.52')}\", page_icon='📈', layout='wide')",
    "st.title('📈 AI Stock Intelligence V1.8.48')":
        "st.title(f\"📈 AI Stock Intelligence V{CFGD.get('mobile_ui_version','1.8.52')}\")",
}
for old,new in replacements.items():
    if old in s:
        s=s.replace(old,new)
    elif new not in s:
        raise SystemExit(f'hard-coded runtime label anchor missing: {old}')

old="    st.caption('Runtime recovery R22.2 • health-gated on-open preliminary • H-10 final 08:50 WIB')\n"
new="""    try:\n        import runtime_health\n        _runtime_health=runtime_health.remote_runtime_health()\n        _runtime_status=str(_runtime_health.get('status') or 'UNKNOWN')\n        _runtime_fp=str(_runtime_health.get('expected_fingerprint') or _runtime_health.get('worker_fingerprint') or '')[:10] or 'unknown'\n        _runtime_build=str(_runtime_health.get('build') or CFGD.get('mobile_ui_version','1.8.52'))\n    except Exception:\n        _runtime_status='UNKNOWN'\n        _runtime_fp='unknown'\n        _runtime_build=str(CFGD.get('mobile_ui_version','1.8.52'))\n    st.caption(f'Runtime {_runtime_build} • {_runtime_status} • fp {_runtime_fp} • on-open health-gated • H-10 final 08:50 WIB')\n"""
if old in s:
    s=s.replace(old,new,1)
elif "on-open health-gated • H-10 final 08:50 WIB" not in s:
    raise SystemExit('premarket runtime label anchor missing')

if 'AI Stock Intelligence V1.8.48' in s:
    raise SystemExit('stale V1.8.48 label remains after patch')

p.write_text(s,encoding='utf-8')
print('R23 DYNAMIC RUNTIME LABEL PATCH V3 APPLIED')
