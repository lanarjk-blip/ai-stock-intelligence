"""Hide old Time Map while a backgrounded WebView resumes and rechecks data."""
def install_resume_guard():
    import streamlit.components.v1 as components
    components.html('''<script>
    const w = window.parent, d = w.document;
    if (!w.__timeMapResumeGuard) {
      w.__timeMapResumeGuard = true;
      const cover = d.createElement('div');
      cover.id = 'timemap-resume-check';
      cover.textContent = 'Memeriksa data pasar terbaru…';
      cover.style.cssText = 'display:none;position:fixed;inset:0;z-index:2147483647;background:white;color:#222;padding:32px;font:18px sans-serif';
      d.body.appendChild(cover);
      let hidden = false;
      d.addEventListener('visibilitychange', () => {
        if (d.hidden) { hidden = true; cover.style.display = 'block'; }
        else if (hidden) { w.location.reload(); }
      });
      w.addEventListener('pageshow', e => {
        if (e.persisted) { cover.style.display='block'; w.location.reload(); }
      });
    }
    </script>''', height=0)
