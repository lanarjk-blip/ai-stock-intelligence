def test_nested_status_strips_render_once_and_refresh_on_next_run(monkeypatch):
    import runtime_health_ui_patch as health
    import storage_housekeeping as storage
    from streamlit.testing.v1 import AppTest
    monkeypatch.setattr(health,'_market_status',lambda:('🟡 Pre-Market','PRE_MARKET'))
    monkeypatch.setattr(health,'_latest_persisted_update',lambda:None)
    reads=[]
    def read_storage():
        reads.append(1)
        return {'status':'NORMAL','db_mb':20.,'last_cleanup_label':'test'}
    monkeypatch.setattr(storage,'storage_health',read_storage)
    app=AppTest.from_string('''
import streamlit as st
import runtime_health_ui_patch as health
import ui_theme_patch as theme
st.session_state['_asi_refresh_strip_drawn']=False
st.session_state['_asi_storage_strip_drawn']=False
for _ in range(5):
    health._render_refresh_status(st)
    status=theme._storage_health()
    if status:
        st.caption('Storage '+status['status'])
''').run(timeout=20)
    assert not app.exception
    assert len(app.button)==1
    assert sum('Sekarang:' in c.value for c in app.caption)==1
    assert sum('Storage NORMAL'==c.value for c in app.caption)==1
    assert len(reads)==1
    app.run(timeout=20)
    assert not app.exception and len(app.button)==1
    assert sum('Sekarang:' in c.value for c in app.caption)==1
    assert sum('Storage NORMAL'==c.value for c in app.caption)==1
    assert len(reads)==2
