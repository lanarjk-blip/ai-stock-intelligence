from pathlib import Path

p=Path('local_runtime_health.py')
s=p.read_text(encoding='utf-8')
s=s.replace(
    'return {**base, "ready": True, "status": status_prefix + "RUNTIME_READY_REFRESHING", "reason": ""}',
    'return {**base, "ready": True, "status": ("LOCAL_RUNTIME_READY_REFRESHING" if status_prefix == "LOCAL_" else "RUNTIME_READY_REFRESHING"), "reason": ""}',
)
s=s.replace(
    'return {**base, "ready": True, "status": status_prefix + "RUNTIME_READY", "reason": ""}',
    'return {**base, "ready": True, "status": ("LOCAL_RUNTIME_READY" if status_prefix == "LOCAL_" else "RUNTIME_READY"), "reason": ""}',
)
if 'RUNTIME_RUNTIME_READY' in s:
    raise SystemExit('double runtime status remains')
p.write_text(s,encoding='utf-8')
print('R25 STATUS NAMES NORMALIZED')
