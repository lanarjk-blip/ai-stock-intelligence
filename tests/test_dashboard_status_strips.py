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


def test_legacy_theme_globals_are_upgraded_without_server_restart(monkeypatch):
    import ui_theme_patch as theme
    import storage_housekeeping as storage
    from streamlit.testing.v1 import AppTest
    monkeypatch.setattr(theme,'_storage_health',lambda:{'status':'LEGACY'})
    monkeypatch.setattr(storage,'storage_health',lambda:{'status':'NORMAL'})
    # Model an already-installed wrapper: its globals remain the module dict.
    legacy_wrapper=type(lambda:None)(compile('_storage_health()','<old-wrapper>','eval'),theme.__dict__)
    assert legacy_wrapper()['status']=='LEGACY'
    app=AppTest.from_string('''
import streamlit as st
import ui_theme_patch
if not getattr(ui_theme_patch._storage_health,'_asi_once_per_render',False):
    import importlib
    importlib.reload(ui_theme_patch)
st.session_state['_asi_storage_strip_drawn']=False
for _ in range(5):
    result=ui_theme_patch._storage_health()
    if result:
        st.caption('Storage '+result['status'])
''').run(timeout=20)
    assert not app.exception
    assert [c.value for c in app.caption]==['Storage NORMAL']
    assert getattr(theme._storage_health,'_asi_once_per_render',False)
    assert legacy_wrapper.__globals__['_storage_health'] is theme._storage_health


def test_cached_wrapper_captions_are_deduplicated_at_output_boundary(monkeypatch):
    import streamlit as st
    from streamlit.testing.v1 import AppTest
    # Restore the process-level API when the test ends.
    monkeypatch.setattr(st,'caption',st.caption)
    app=AppTest.from_string('''
import streamlit as st
from dashboard_status import install_storage_caption_guard
st.session_state['_asi_storage_caption_emitted']=False
install_storage_caption_guard(st)
for i in range(15):
    st.caption('Storage NORMAL • DB 20.0 MB')
st.caption('Other status stays visible')
''').run(timeout=20)
    assert not app.exception
    assert [c.value for c in app.caption]==['Storage NORMAL • DB 20.0 MB','Other status stays visible']
    app.run(timeout=20)
    assert not app.exception
    assert [c.value for c in app.caption]==['Storage NORMAL • DB 20.0 MB','Other status stays visible']
