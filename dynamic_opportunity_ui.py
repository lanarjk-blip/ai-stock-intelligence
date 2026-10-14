"""Actionable Dynamic Opportunity UI.

V1.8.52 keeps the production Top Opportunity surface behind the DATA_QUALITY
publication gate, normalizes stale shell version labels from config.json, and
renders a compact decision-first UI without turning MONITOR candidates into
fake BUY signals.
"""
from __future__ import annotations

import html
import json
import os
import sqlite3
from urllib.parse import quote

import pandas as pd
import streamlit as st

RUNTIME_BUILD_MARKER = 'V1.8.52-20261015-R20'


def _install_runtime_version_patch():
    """Replace stale shell labels with the configured runtime version."""
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
        pass


def _install_compact_tab_patch():
    """Turn the legacy seven-tab shell into the agreed five-tab navigation.

    Pre-Market is appended to Home. AI/Validation and Engine/Log are appended
    to Lainnya. The app code can keep its seven context variables while the
    user sees only five stable destinations.
    """
    try:
        original_tabs = st.tabs
        if getattr(original_tabs, '_asi_compact_tabs', False):
            return

        legacy = [
            '🏠 Ringkasan', '🌅 Pre-Market', '🚀 Opportunity', '🔎 Analisis Saham',
            '📚 History & Shadow', '🧠 AI & Validasi', '⚙️ Engine / Log'
        ]

        def tabs(labels, *args, **kwargs):
            labels_list = list(labels)
            if labels_list == legacy:
                compact = original_tabs(
                    ['🏠 Home', '🚀 Opportunity', '🔎 Analisis', '📚 History', '⚙️ Lainnya'],
                    *args, **kwargs
                )
                home, opportunity, analysis, history, more = compact
                return [home, home, opportunity, analysis, history, more, more]
            return original_tabs(labels, *args, **kwargs)

        tabs._asi_compact_tabs = True
        st.tabs = tabs
    except Exception:
        pass


_install_runtime_version_patch()
_install_compact_tab_patch()


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


def _price(value):
    try:
        return f'Rp {float(value):,.0f}'
    except Exception:
        return '—'


def _range_price(low, high):
    try:
        lo = float(low)
        hi = float(high)
        return f'Rp {lo:,.0f} - {hi:,.0f}'
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


def _status_meta(state, entry_status, candidate_mode=''):
    state = _text(state, '').upper()
    entry = _text(entry_status, '').upper()
    mode = _text(candidate_mode, '').upper()
    if mode == 'MONITOR_FALLBACK':
        return '⚪', 'MONITOR', 'WAIT', 'watch', 'MONITOR ONLY'
    if state == 'ENTRY READY' and entry == 'ACTIVE':
        return '🟢', 'ENTRY READY', 'ACTIVE', 'ready', 'BUY WINDOW'
    if state == 'DEVELOPING':
        return '🟡', 'DEVELOPING', entry or 'WAIT', 'developing', 'WAIT'
    if state == 'WATCH':
        return '👀', 'WATCH', entry or 'NEUTRAL', 'watch', 'WAIT'
    return '⚪', state or 'MONITOR', entry or 'NEUTRAL', 'watch', 'WAIT'


def _css():
    st.markdown('''
<style>
.asi-live-note{padding:12px 14px;border:1px solid rgba(128,128,128,.28);border-radius:14px;margin:.25rem 0 .8rem;font-size:.94rem}
.asi-live-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(280px,1fr));gap:10px;margin:.45rem 0 1rem}
.asi-live-card{border:1px solid rgba(128,128,128,.25);border-radius:17px;padding:14px 15px;background:rgba(128,128,128,.055)}
.asi-live-card.ready{border-color:rgba(46,204,113,.70);background:rgba(46,204,113,.09)}
.asi-live-card.developing{border-color:rgba(241,196,15,.68);background:rgba(241,196,15,.08)}
.asi-live-card.watch{border-color:rgba(128,128,128,.32)}
.asi-live-head{display:flex;align-items:center;justify-content:space-between;gap:8px;margin-bottom:8px}
.asi-live-ticker{font-size:1.25rem;font-weight:850;letter-spacing:.2px;text-decoration:none!important;color:inherit!important;min-height:38px;display:flex;align-items:center}
.asi-live-rank{font-size:.82rem;opacity:.68}
.asi-action{display:inline-block;border-radius:999px;padding:6px 10px;font-size:.78rem;font-weight:850;margin-bottom:8px;border:1px solid rgba(128,128,128,.25)}
.asi-live-card.ready .asi-action{border-color:rgba(46,204,113,.55)}
.asi-live-state{font-size:.95rem;font-weight:750;margin-bottom:10px;opacity:.9}
.asi-price{font-size:1.38rem;font-weight:850;margin-bottom:9px}
.asi-levels{display:grid;grid-template-columns:repeat(2,1fr);gap:7px;margin-bottom:9px}
.asi-level{border:1px solid rgba(128,128,128,.18);border-radius:10px;padding:7px 8px;font-size:.72rem;opacity:.76}
.asi-level b{display:block;font-size:.91rem;opacity:1;margin-top:2px}
.asi-live-kpis{display:grid;grid-template-columns:repeat(4,1fr);gap:7px}
.asi-live-kpi{font-size:.70rem;opacity:.72}.asi-live-kpi b{display:block;font-size:.93rem;opacity:1;margin-top:1px}
.asi-live-meta{margin-top:9px;font-size:.76rem;opacity:.72;line-height:1.35}
.asi-live-warning{margin-top:9px;font-size:.82rem;font-weight:750}
@media(max-width:700px){
  .asi-live-grid{grid-template-columns:1fr;gap:9px}.asi-live-card{padding:13px 13px}.asi-live-ticker{font-size:1.2rem}
  .asi-price{font-size:1.28rem}.asi-levels{grid-template-columns:repeat(2,1fr)}.asi-live-kpis{grid-template-columns:repeat(4,1fr)}
}
</style>
''', unsafe_allow_html=True)


def render_actionable_dynamic(dyn, render_table=None, table_limit=None):
    """Render production Top Opportunity only when DATA_QUALITY is READY."""
    _css()
    quality = _quality_gate_state()
    st.caption(
        f'Runtime build {RUNTIME_BUILD_MARKER} • Quality {quality["status"]} • '
        f'Ready coverage {_ratio_label(quality.get("ready_ratio"))}'
    )
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
        '<div class="asi-live-note"><b>Keputusan dulu, angka belakangan.</b> '
        '<b>BUY WINDOW</b> hanya muncul bila status ENTRY READY dan Entry Window ACTIVE. '
        '<b>WAIT / MONITOR ONLY</b> bukan perintah beli.</div>',
        unsafe_allow_html=True,
    )

    risk_high = dyn.get('risk', pd.Series(dtype=str)).astype(str).str.upper().eq('HIGH').any() if 'risk' in dyn else False
    bearish = dyn.get('market_regime', pd.Series(dtype=str)).astype(str).str.upper().eq('BEARISH').any() if 'market_regime' in dyn else False
    if risk_high and bearish:
        st.warning('⚠️ Mode konservatif: terdapat Risk HIGH saat IHSG BEARISH. Prioritaskan BUY WINDOW yang benar-benar ACTIVE; kandidat lain tetap WAIT.')

    cards = []
    for pos, (_, row) in enumerate(dyn.head(5).iterrows(), start=1):
        icon, state_label, entry_label, css_kind, action_label = _status_meta(
            row.get('state'), row.get('entry_status'), row.get('candidate_mode')
        )
        ticker_raw = _text(row.get('ticker'), '?').upper()
        ticker = html.escape(ticker_raw)
        ticker_url = '?ticker=' + quote(ticker_raw)
        rank = row.get('rank', row.get('opportunity_rank', pos))
        p_up = _pct(row.get('p_up'))
        conf = _pct(row.get('confidence'))
        score = _num(row.get('opportunity_score'))
        rr = _num(row.get('risk_reward'), 2)
        risk = html.escape(_text(row.get('risk')))
        regime = html.escape(_text(row.get('market_regime')))
        generated = html.escape(_text(row.get('generated_at'), ''))
        warning = ''
        if risk.upper() == 'HIGH' and regime.upper() == 'BEARISH':
            warning = '<div class="asi-live-warning">⚠️ HIGH risk • IHSG BEARISH</div>'
        cards.append(
            f'<div class="asi-live-card {css_kind}">'
            f'<div class="asi-live-head"><a class="asi-live-ticker" href="{ticker_url}" target="_self">{icon} {ticker}</a><span class="asi-live-rank">#{html.escape(str(rank))}</span></div>'
            f'<div class="asi-action">{html.escape(action_label)}</div>'
            f'<div class="asi-live-state">{html.escape(state_label)} • Entry {html.escape(entry_label)}</div>'
            f'<div class="asi-price">{html.escape(_price(row.get("close")))}</div>'
            f'<div class="asi-levels">'
            f'<div class="asi-level">ENTRY<b>{html.escape(_range_price(row.get("entry_low"), row.get("entry_high")))}</b></div>'
            f'<div class="asi-level">STOP LOSS<b>{html.escape(_price(row.get("stop_loss")))}</b></div>'
            f'<div class="asi-level">TARGET 1<b>{html.escape(_price(row.get("target_1")))}</b></div>'
            f'<div class="asi-level">TARGET 2<b>{html.escape(_price(row.get("target_2")))}</b></div>'
            f'</div>'
            f'<div class="asi-live-kpis">'
            f'<div class="asi-live-kpi">P(UP)<b>{p_up}</b></div>'
            f'<div class="asi-live-kpi">CONF.<b>{conf}</b></div>'
            f'<div class="asi-live-kpi">SCORE<b>{score}</b></div>'
            f'<div class="asi-live-kpi">R/R<b>{rr}</b></div>'
            f'</div>'
            f'<div class="asi-live-meta">Risk <b>{risk}</b> • IHSG <b>{regime}</b>'
            + (f' • snapshot {generated[:19].replace("T", " ")}' if generated else '')
            + f'</div>{warning}</div>'
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
            'time_confidence':'Time Conf. %','candidate_mode':'Candidate Mode'
        })
        cols = [c for c in ['rank','ticker','company','Opportunity Score','State','Candidate Mode','Entry Window','Horizon','IHSG Regime',
                            'Price','Entry Low','Entry High','Stop Loss','Target 1','Target 2','R/R','Risk','AI Score',
                            'P(UP) %','P(DOWN) %','AI Conf. %','Relative %','Time Edge %','Time Conf. %'] if c in dshow.columns]
        if table_limit:
            dshow = dshow.head(int(table_limit))
        render_table(dshow, cols)

    st.caption('Top-5 bersifat dinamis dan probabilistik. BUY WINDOW memerlukan ENTRY READY + ACTIVE; WAIT/MONITOR tidak boleh dibaca sebagai rekomendasi beli.')