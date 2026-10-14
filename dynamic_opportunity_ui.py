"""Actionable Dynamic Opportunity UI.

V1.8.52 makes the user-facing Top Opportunity surface obey the production
DATA_QUALITY publication gate. It also normalizes stale hard-coded UI version
labels to the version declared by config.json while the legacy app shell is
being retired.
"""
from __future__ import annotations

import html
import json
import os
import sqlite3
import pandas as pd
import streamlit as st


def _install_runtime_version_patch():
    """Replace stale V1.8.48 labels with the configured runtime version."""
    try:
        cfg_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'config.json')
        with open(cfg_path, 'r', encoding='utf-8') as fh:
            cfg = json.load(fh)
        version = str(cfg.get('mobile_ui_version') or cfg.get('engine_version') or '1.8.52')
        wanted = f'AI Stock Intelligence V{version}'

        original_title = st.title
        original_page_config = st.set_page_config

        if not getattr(original_title, '_asi_v1852_patch', False):
            def title(value, *args, **kwargs):
                if isinstance(value, str) and value.startswith('📈 AI Stock Intelligence V'):
                    value = f'📈 {wanted}'
                return original_title(value, *args, **kwargs)
            title._asi_v1852_patch = True
            st.title = title

        if not getattr(original_page_config, '_asi_v1852_patch', False):
            def set_page_config(*args, **kwargs):
                if isinstance(kwargs.get('page_title'), str) and kwargs['page_title'].startswith('AI Stock Intelligence V'):
                    kwargs['page_title'] = wanted
                return original_page_config(*args, **kwargs)
            set_page_config._asi_v1852_patch = True
            st.set_page_config = set_page_config
    except Exception:
        # Version normalization must never make the dashboard unavailable.
        pass


_install_runtime_version_patch()


def _text(value, default='—'):
    if value is None or str(value).lower() in {'nan', 'none', ''}:
        return default
    return str(value)


def _pct(value):
    try:
        x = float(value)
        if x <= 1:
            x *= 100
        return f'{x:.1f}%'
    except Exception:
        return '—'


def _num(value, digits=1):
    try:
        return f'{float(value):.{digits}f}'
    except Exception:
        return '—'


def _quality_gate_state():
    """Read the worker's persisted publication gate without importing app.py."""
    db = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'db', 'market.db')
    try:
        con = sqlite3.connect(db, timeout=5)
        rows = con.execute(
            "SELECT key,value FROM state WHERE key IN "
            "('data_quality_status','data_quality_collection_ratio','data_quality_ready_ratio','data_quality_reasons')"
        ).fetchall()
        con.close()
        state = {str(k): str(v) for k, v in rows}
        status = state.get('data_quality_status', '').upper()
        return {
            'known': bool(status),
            'ready': status == 'READY',
            'status': status or 'UNKNOWN',
            'collection_ratio': state.get('data_quality_collection_ratio', ''),
            'ready_ratio': state.get('data_quality_ready_ratio', ''),
            'reasons': state.get('data_quality_reasons', ''),
        }
    except Exception:
        return {'known': False, 'ready': False, 'status': 'UNKNOWN', 'collection_ratio': '', 'ready_ratio': '', 'reasons': ''}


def _ratio_label(value):
    try:
        return f'{float(value) * 100:.1f}%'
    except Exception:
        return '—'


def _status_meta(state, entry_status):
    state = _text(state, '').upper()
    entry = _text(entry_status, '').upper()
    if state == 'ENTRY READY' and entry == 'ACTIVE':
        return '🟢', 'ENTRY READY', 'ACTIVE', 'ready'
    if state == 'DEVELOPING':
        return '🟡', 'DEVELOPING', entry or 'WAIT', 'developing'
    if state == 'WATCH':
        return '👀', 'WATCH', entry or 'NEUTRAL', 'watch'
    return '⚪', state or 'MONITOR', entry or 'NEUTRAL', 'watch'


def _css():
    st.markdown('''
<style>
.asi-live-note{padding:12px 14px;border:1px solid rgba(128,128,128,.28);border-radius:14px;margin:.25rem 0 .8rem;font-size:.94rem}
.asi-live-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:10px;margin:.45rem 0 1rem}
.asi-live-card{border:1px solid rgba(128,128,128,.25);border-radius:16px;padding:14px 15px;background:rgba(128,128,128,.055)}
.asi-live-card.ready{border-color:rgba(46,204,113,.70);background:rgba(46,204,113,.09)}
.asi-live-card.developing{border-color:rgba(241,196,15,.68);background:rgba(241,196,15,.08)}
.asi-live-card.watch{border-color:rgba(128,128,128,.32)}
.asi-live-head{display:flex;align-items:center;justify-content:space-between;gap:8px;margin-bottom:8px}
.asi-live-ticker{font-size:1.24rem;font-weight:800;letter-spacing:.2px}
.asi-live-rank{font-size:.82rem;opacity:.65}
.asi-live-state{font-size:1.02rem;font-weight:800;margin-bottom:8px}
.asi-live-kpis{display:grid;grid-template-columns:repeat(3,1fr);gap:7px}
.asi-live-kpi{font-size:.78rem;opacity:.72}.asi-live-kpi b{display:block;font-size:1rem;opacity:1;margin-top:1px}
.asi-live-warning{margin-top:9px;font-size:.82rem;font-weight:700}
@media(max-width:700px){.asi-live-grid{grid-template-columns:1fr}.asi-live-ticker{font-size:1.18rem}.asi-live-kpis{grid-template-columns:repeat(3,1fr)}}
</style>
''', unsafe_allow_html=True)


def render_actionable_dynamic(dyn, render_table=None, table_limit=None):
    """Render production Top Opportunity only when DATA_QUALITY is READY."""
    _css()
    quality = _quality_gate_state()
    if quality['known'] and not quality['ready']:
        collection = _ratio_label(quality.get('collection_ratio'))
        ready = _ratio_label(quality.get('ready_ratio'))
        st.warning(
            f"🛡️ **DATA NOT READY • Top-5 produksi ditahan.** "
            f"Collection coverage **{collection}**, ready-symbol coverage **{ready}**. "
            "Kandidat bootstrap/diagnostik tidak ditampilkan sebagai rekomendasi."
        )
        if quality.get('reasons'):
            st.caption('Quality gate: ' + quality['reasons'])
        return

    if dyn is None or dyn.empty:
        st.info('Belum ada kandidat Dynamic Opportunity.')
        return

    if not quality['known']:
        st.info('🛡️ Quality Gate belum memiliki status final. Top-5 ditahan sampai pemeriksaan data selesai.')
        return

    st.markdown(
        '<div class="asi-live-note"><b>Top Opportunity ≠ Top BUY.</b> '
        'Ranking menunjukkan peluang relatif. Kondisi actionable hanya ketika '
        '<b>🟢 ENTRY READY</b> dan <b>Entry Window ACTIVE</b>. '
        'DEVELOPING/WATCH masih menunggu konfirmasi.</div>',
        unsafe_allow_html=True,
    )

    risk_high = dyn.get('risk', pd.Series(dtype=str)).astype(str).str.upper().eq('HIGH').any() if 'risk' in dyn else False
    bearish = dyn.get('market_regime', pd.Series(dtype=str)).astype(str).str.upper().eq('BEARISH').any() if 'market_regime' in dyn else False
    if risk_high and bearish:
        st.warning('⚠️ Mode konservatif: terdapat Risk HIGH saat IHSG BEARISH. Prioritaskan ENTRY READY / ACTIVE; WATCH dan DEVELOPING bukan sinyal beli.')

    cards = []
    for pos, (_, row) in enumerate(dyn.head(5).iterrows(), start=1):
        icon, state_label, entry_label, css_kind = _status_meta(row.get('state'), row.get('entry_status'))
        ticker = html.escape(_text(row.get('ticker'), '?'))
        rank = row.get('rank', row.get('opportunity_rank', pos))
        p_up = _pct(row.get('p_up'))
        score = _num(row.get('opportunity_score'))
        rr = _num(row.get('risk_reward'), 2)
        risk = html.escape(_text(row.get('risk')))
        regime = html.escape(_text(row.get('market_regime')))
        warning = ''
        if risk.upper() == 'HIGH' and regime.upper() == 'BEARISH':
            warning = '<div class="asi-live-warning">⚠️ HIGH risk • IHSG BEARISH</div>'
        cards.append(
            f'<div class="asi-live-card {css_kind}">'
            f'<div class="asi-live-head"><span class="asi-live-ticker">{icon} {ticker}</span><span class="asi-live-rank">#{html.escape(str(rank))}</span></div>'
            f'<div class="asi-live-state">{html.escape(state_label)} • {html.escape(entry_label)}</div>'
            f'<div class="asi-live-kpis">'
            f'<div class="asi-live-kpi">P(UP)<b>{p_up}</b></div>'
            f'<div class="asi-live-kpi">Score<b>{score}</b></div>'
            f'<div class="asi-live-kpi">R/R<b>{rr}</b></div>'
            f'</div>{warning}</div>'
        )
    st.markdown('<div class="asi-live-grid">' + ''.join(cards) + '</div>', unsafe_allow_html=True)

    if render_table is not None:
        dshow = dyn.copy()
        for c in ['opportunity_score', 'ai_score']:
            if c in dshow:
                dshow[c] = pd.to_numeric(dshow[c], errors='coerce').round(1)
        for c in ['p_up', 'p_down', 'confidence', 'relative_percentile', 'time_edge', 'time_confidence']:
            if c in dshow:
                dshow[c] = (pd.to_numeric(dshow[c], errors='coerce') * 100).round(1)
        dshow = dshow.rename(columns={
            'opportunity_score':'Opportunity Score','market_regime':'IHSG Regime','state':'State',
            'entry_status':'Entry Window','horizon':'Horizon','close':'Price','entry_low':'Entry Low',
            'entry_high':'Entry High','stop_loss':'Stop Loss','target_1':'Target 1','target_2':'Target 2',
            'risk_reward':'R/R','risk':'Risk','ai_score':'AI Score','p_up':'P(UP) %','p_down':'P(DOWN) %',
            'confidence':'AI Conf. %','relative_percentile':'Relative %','time_edge':'Time Edge %',
            'time_confidence':'Time Conf. %'
        })
        cols = [c for c in ['rank','ticker','company','Opportunity Score','State','Entry Window','Horizon','IHSG Regime',
                            'Price','Entry Low','Entry High','Stop Loss','Target 1','Target 2','R/R','Risk','AI Score',
                            'P(UP) %','P(DOWN) %','AI Conf. %','Relative %','Time Edge %','Time Conf. %'] if c in dshow.columns]
        if table_limit:
            dshow = dshow.head(int(table_limit))
        render_table(dshow, cols)

    st.caption('Top-5 bersifat dinamis dan probabilistik. Kandidat dapat masuk/keluar ketika kondisi berubah; status WATCH/DEVELOPING bukan perintah beli.')