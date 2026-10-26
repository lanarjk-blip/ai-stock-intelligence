"""Deduplicate output even from title wrappers retained across hot deployments."""
def install_storage_caption_guard(st):
    if getattr(st.caption,'_asi_storage_caption_once',False):
        return
    original=st.caption
    def caption(body,*args,**kwargs):
        if not str(body).startswith('Storage '):
            return original(body,*args,**kwargs)
        # Nested guard layers must allow the same first call to reach Streamlit.
        depth=st.session_state.get('_asi_storage_caption_depth',0)
        if depth:
            return original(body,*args,**kwargs)
        if st.session_state.get('_asi_storage_caption_emitted',False):
            return None
        st.session_state['_asi_storage_caption_emitted']=True
        st.session_state['_asi_storage_caption_depth']=depth+1
        try:
            return original(body,*args,**kwargs)
        finally:
            st.session_state['_asi_storage_caption_depth']=depth
    caption._asi_storage_caption_once=True
    st.caption=caption
