"""Two-lane setup screening from saved data; no price requests or training."""
import html
import math
import re
import sqlite3
from urllib.parse import quote

import pandas as pd

from trading_monitor import _stamp, _date_label


def latest_candles(db_path):
    """One read-only local query, rather than fetching every ticker's history."""
    try:
        con=sqlite3.connect(f'file:{db_path}?mode=ro',uri=True,timeout=3)
        try:
            return pd.read_sql_query('''SELECT ticker,ts,open,close,volume,source FROM (
                SELECT ticker,ts,open,close,volume,source,
                       ROW_NUMBER() OVER (PARTITION BY ticker ORDER BY julianday(ts) DESC) AS n
                FROM intraday) WHERE n=1''',con)
        finally:
            con.close()
    except (sqlite3.Error,pd.errors.DatabaseError):
        return pd.DataFrame()


def _symbol(value):
    text=str(value).upper().strip().removesuffix('.JK')
    return text if re.fullmatch(r'[A-Z0-9]{2,6}',text) else None


def _num(value):
    try:
        n=float(value)
        return n if math.isfinite(n) else None
    except (TypeError,ValueError):
        return None


def split_groups(universe,dynamic,candles,session,quality,threshold_pct=.2):
    """Candidate means operational setup readiness, never validated trading edge."""
    now=_stamp(session.get('wib'))
    if now is None:
        raise ValueError('Screen requires an aware WIB clock')
    def mapping(frame):
        if frame is None or frame.empty or 'ticker' not in frame:
            return {}
        out={}
        for row in frame.to_dict('records'):
            ticker=_symbol(row.get('ticker'))
            if ticker:
                # Conflicting snapshots cannot become actionable by row order.
                out[ticker]=row if ticker not in out else None
        return out
    dynamic_map=mapping(dynamic);candle_map=mapping(candles)
    symbols={_symbol(v) for v in universe.get('ticker',pd.Series(dtype=str))} if universe is not None else set()
    symbols.discard(None)
    accepted=[];waiting=[]
    from market_session import session_status
    for ticker in sorted(symbols):
        row=dynamic_map.get(ticker);bar=candle_map.get(ticker)
        reason='';stamp=_stamp(bar.get('ts')) if bar else None
        generated=_stamp(row.get('generated_at')) if row else None
        price=_num(bar.get('close')) if bar else None
        if not session.get('calendar_verified'):
            reason='Kalender belum terverifikasi'
        elif not session.get('live'):
            reason='Menunggu sesi perdagangan; bukan live'
        elif not quality.get('known') or not quality.get('ready'):
            reason='Kualitas data belum READY'
        elif not row:
            reason='Belum ada kandidat Dynamic yang terverifikasi'
        elif str(row.get('candidate_mode')).upper()!='STRICT':
            reason='MONITOR / kandidat belum lolos filter utama'
        elif str(row.get('state')).upper()!='ENTRY READY' or str(row.get('entry_status')).upper()!='ACTIVE':
            reason='Setup / jendela entry belum aktif'
        elif str(row.get('decision')).upper() not in {'BUY','STRONG_BUY','NEAR ENTRY'}:
            reason='Keputusan engine masih WAIT / WATCH / AVOID'
        elif stamp is None or price is None or price<=0:
            reason='Candle bertimestamp belum tersedia'
        elif stamp.date()!=now.date():
            reason='Candle hari sebelumnya; menunggu data hari ini'
        elif session_status(now=stamp)['status']!=session['status']:
            reason='Candle berasal dari sesi berbeda'
        elif not 0 <= (now-stamp).total_seconds()/60 <= 5:
            reason='Candle belum cukup baru untuk pantauan per menit'
        elif (not isinstance(bar.get('source'),str) or not bar['source'].strip()
              or str(bar['source']).upper() in {'UNKNOWN','UNAVAILABLE','NAN','NONE'}
              or 'CACHE' in str(bar['source']).upper()):
            reason='Sumber candle cadangan / belum terverifikasi'
        elif _num(bar.get('volume')) is None or float(bar['volume'])<=0:
            reason='Volume transaksi belum tersedia'
        elif _num(bar.get('open')) is None or float(bar['open'])<=0:
            reason='Harga pembukaan candle belum terverifikasi'
        elif (price/float(bar['open'])-1)*100 < -threshold_pct:
            reason='Candle berjalan melemah; setup belum terkonfirmasi'
        elif generated is None or not 0 <= (now-generated).total_seconds()/60 <= 5 or generated<stamp:
            reason='Snapshot Dynamic kedaluwarsa / belum mengikuti candle'
        elif _num(row.get('close')) is None or not math.isclose(float(row['close']),price,rel_tol=1e-9,abs_tol=1e-6):
            reason='Harga berubah sejak snapshot Dynamic'
        elif str(row.get('risk')).upper() not in {'LOW','MEDIUM'}:
            reason='Risiko tinggi atau belum diketahui'
        elif str(row.get('market_regime')).upper() not in {'BULLISH','SIDEWAYS','NEUTRAL'}:
            reason='IHSG defensif / volatilitas tinggi / belum diketahui'
        else:
            lo,hi,stop,target=(_num(row.get(c)) for c in ['entry_low','entry_high','stop_loss','target_1'])
            rr=_num(row.get('risk_reward'))
            if None in (lo,hi,stop,target,rr) or not 0 < stop < price < target or not lo<=price<=hi:
                reason='Zona entry atau batas risiko belum sesuai harga'
            elif rr<1.5 or (target-price)/(price-stop)<1.5:
                reason='Rasio potensi hasil terhadap risiko belum cukup'
        record={'Ticker':ticker,'Alasan':reason or 'Setup aktif; data dan batas risiko lolos saringan awal',
                'Candle WIB':stamp.strftime('%d-%m-%Y %H:%M') if stamp is not None else 'Belum tersedia'}
        (waiting if reason else accepted).append(record)
    columns=['Ticker','Alasan','Candle WIB']
    return pd.DataFrame(accepted,columns=columns),pd.DataFrame(waiting,columns=columns)


def render_groups(universe,dynamic,candles,session,quality,key,threshold_pct=.2):
    import streamlit as st
    accepted,waiting=split_groups(universe,dynamic,candles,session,quality,threshold_pct)
    now=_stamp(session['wib'])
    st.markdown('### ⚖️ Pilih ticker untuk dipantau')
    st.caption(f"Diperiksa {_date_label(now.date())}, {now:%H:%M:%S} WIB • {session['status']} • {len(accepted)+len(waiting)} ticker • otomatis 90 detik")
    st.caption('Kandidat = lolos saringan awal data, setup dan risiko. Bukan bukti akurasi, jaminan untung atau perintah beli; Time Map tetap NO EDGE bila prediksi belum tervalidasi.')
    query=st.text_input('Cari kode di dua kelompok',key=key,placeholder='Contoh: BBCA, ANTM').upper().strip()
    if query:
        accepted=accepted[accepted.Ticker.str.contains(query,regex=False)]
        waiting=waiting[waiting.Ticker.str.contains(query,regex=False)]
    def lane(frame,title,kind):
        rows=[]
        for row in frame.head(40).to_dict('records'):
            ticker=row['Ticker']
            rows.append(f'<div class="tg-row"><a href="?ticker={quote(ticker)}" target="_self">{html.escape(ticker)}</a>'
                        f'<span>{html.escape(row["Alasan"])}</span><small>Candle {html.escape(row["Candle WIB"])}</small></div>')
        content=''.join(rows) or '<p>Belum ada ticker pada kelompok ini.</p>'
        if len(frame)>40:
            content+=f'<small>Tampil 40 dari {len(frame)} ticker. Gunakan pencarian untuk ticker lainnya.</small>'
        return f'<section class="tg-lane {kind}"><h4>{title} · {len(frame)}</h4><div class="tg-scroll">{content}</div></section>'
    st.markdown('''<style>
    .tg-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:10px;margin:10px 0}
    .tg-lane{border:1px solid #8885;border-radius:12px;padding:10px;min-width:0}
    .tg-lane h4{font-size:.95rem;margin:0 0 8px}.tg-good h4{color:#13a56e}.tg-wait h4{color:#d89b35}
    .tg-scroll{max-height:340px;overflow-y:auto}.tg-row{padding:8px 0;border-top:1px solid #8883}
    .tg-row a{font-weight:750;display:block;text-decoration:none}.tg-row span{display:block;font-size:.83rem;overflow-wrap:anywhere}
    .tg-row small{display:block;font-size:.70rem;opacity:.7}
    </style><div class="tg-grid">'''+lane(accepted,'🟢 Kandidat trading','tg-good')+lane(waiting,'⚪ Belum layak / WAIT','tg-wait')+'</div>',unsafe_allow_html=True)
    return accepted,waiting
