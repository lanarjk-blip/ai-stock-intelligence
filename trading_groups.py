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
        elif generated is None or not 0 <= (now-generated).total_seconds()/60 <= 5 or generated<stamp:
            reason='Snapshot Dynamic kedaluwarsa / belum mengikuti candle'
        elif _num(row.get('close')) is None or not math.isclose(float(row['close']),price,rel_tol=1e-9,abs_tol=1e-6):
            reason='Harga berubah sejak snapshot Dynamic'
        elif any(not isinstance(row.get(c),str) or not row[c].strip() or row[c].upper() in {'UNKNOWN','NONE','NAN','UNAVAILABLE'}
                 for c in ['candidate_mode','state','entry_status','decision','risk','market_regime']):
            reason='Data setup / risiko belum lengkap'
        elif any(_num(row.get(c)) is None for c in ['entry_low','entry_high','stop_loss','target_1','risk_reward']):
            reason='Angka entry / batas risiko belum lengkap'
        else:
            reason=None
        # Missing/stale evidence is not a rejection of the stock's setup.
        group='Belum bisa dinilai' if reason else 'Lolos saringan'
        if not reason:
            if str(row.get('candidate_mode')).upper()!='STRICT':
                reason='MONITOR / kandidat belum lolos filter utama'
            elif str(row.get('state')).upper()!='ENTRY READY' or str(row.get('entry_status')).upper()!='ACTIVE':
                reason='Setup / jendela entry belum aktif'
            elif str(row.get('decision')).upper() not in {'BUY','STRONG_BUY','NEAR ENTRY'}:
                reason='Keputusan engine masih WAIT / WATCH / AVOID'
            elif (price/float(bar['open'])-1)*100 < -threshold_pct:
                reason='Candle berjalan melemah; setup belum terkonfirmasi'
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
            if reason:
                group='Tidak lolos'
        record={'Ticker':ticker,'Kelompok':group,'Alasan':reason or 'Setup aktif; data dan batas risiko lolos saringan awal',
                'Candle WIB':stamp.strftime('%d-%m-%Y %H:%M') if stamp is not None else 'Belum tersedia'}
        (waiting if reason else accepted).append(record)
    columns=['Ticker','Kelompok','Alasan','Candle WIB']
    return pd.DataFrame(accepted,columns=columns),pd.DataFrame(waiting,columns=columns)


def group_summary(accepted,waiting):
    """Universe shares, not predictive probabilities; rounded total exactly 100%."""
    labels=['Lolos saringan','Tidak lolos','Belum bisa dinilai']
    counts=[len(accepted),int((waiting.Kelompok=='Tidak lolos').sum()),
            int((waiting.Kelompok=='Belum bisa dinilai').sum())]
    total=sum(counts)
    units=[n*10000//total if total else 0 for n in counts]
    if total:
        order=sorted(range(3),key=lambda i: (-(counts[i]*10000%total),i))
        for i in order[:10000-sum(units)]:
            units[i]+=1
    return pd.DataFrame({'Kelompok':labels,'Jumlah':counts,
                         'Persentase':[f'{n/100:.2f}%' for n in units]})


def render_groups(universe,dynamic,candles,session,quality,key,threshold_pct=.2):
    import streamlit as st
    accepted,waiting=split_groups(universe,dynamic,candles,session,quality,threshold_pct)
    now=_stamp(session['wib'])
    st.markdown('### ⚖️ Pilih ticker untuk dipantau')
    st.caption(f"Diperiksa {_date_label(now.date())}, {now:%H:%M:%S} WIB • {session['status']} • {len(accepted)+len(waiting)} ticker • otomatis 90 detik")
    st.table(group_summary(accepted,waiting))
    if not session.get('live'):
        st.info('Penilaian live belum berjalan. Menunggu sesi perdagangan; saham belum dinyatakan tidak layak.')
    st.caption('Persentase di atas = bagian dari seluruh ticker, bukan peluang untung. Akurasi kelompok lolos: BELUM TERBUKTI; belum ada evaluasi hasil transaksi kelompok ini. Tidak lolos bukan berarti pasti turun. Time Map tetap NO EDGE bila prediksi belum tervalidasi.')
    st.caption('Saringan awal: candle hari/sesi yang sama, umur maksimal 5 menit dari awal candle, snapshot sesuai harga, setup aktif, risiko LOW/MEDIUM dan R/R minimal 1,5. Data terlambat atau belum lengkap masuk BELUM BISA DINILAI.')
    with st.expander('Mengapa belum lolos? Jumlah dan alasan'):
        reasons=waiting.groupby(['Kelompok','Alasan']).size().reset_index(name='Jumlah')
        st.dataframe(reasons,hide_index=True,use_container_width=True)
    query=st.text_input('Cari kode di dua kelompok',key=key,placeholder='Contoh: BBCA, ANTM').upper().strip()
    left=accepted;right=waiting
    if query:
        left=left[left.Ticker.str.contains(query,regex=False)]
        right=right[right.Ticker.str.contains(query,regex=False)]
    page_columns=st.columns(2)
    pages=[]
    for i,frame in enumerate([left,right]):
        with page_columns[i]:
            pages.append(st.selectbox('Halaman kiri' if i==0 else 'Halaman kanan',
                         range(1,max(1,math.ceil(len(frame)/40))+1),key=f'{key}_page_{i}'))
    def lane(frame,title,kind,page):
        rows=[]
        start=(page-1)*40
        for row in frame.iloc[start:start+40].to_dict('records'):
            ticker=row['Ticker']
            badge={'Lolos saringan':'🟢 LOLOS','Tidak lolos':'🔴 TIDAK LOLOS','Belum bisa dinilai':'⚪ BELUM BISA DINILAI'}[row['Kelompok']]
            rows.append(f'<div class="tg-row"><a href="?ticker={quote(ticker)}" target="_self">{html.escape(ticker)}</a>'
                        f'<strong>{badge}</strong><span>{html.escape(row["Alasan"])}</span><small>Candle {html.escape(row["Candle WIB"])}</small></div>')
        content=''.join(rows) or '<p>Belum ada ticker pada kelompok ini.</p>'
        if len(frame):
            content+=f'<small>Tampil {start+1}–{min(start+40,len(frame))} dari {len(frame)} ticker. Semua tersedia melalui pilihan halaman.</small>'
        return f'<section class="tg-lane {kind}"><h4>{title} · {len(frame)}</h4><div class="tg-scroll">{content}</div></section>'
    st.markdown('''<style>
    .tg-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:10px;margin:10px 0}
    .tg-lane{border:1px solid #8885;border-radius:12px;padding:10px;min-width:0}
    .tg-lane h4{font-size:.95rem;margin:0 0 8px}.tg-good h4{color:#13a56e}.tg-wait h4{color:#d89b35}
    .tg-scroll{max-height:340px;overflow-y:auto}.tg-row{padding:8px 0;border-top:1px solid #8883}
    .tg-row a{font-weight:750;display:block;text-decoration:none}.tg-row span{display:block;font-size:.83rem;overflow-wrap:anywhere}
    .tg-row strong{font-size:.72rem}.tg-row small{display:block;font-size:.70rem;opacity:.7}
    </style><div class="tg-grid">'''+lane(left,'🟢 Lolos saringan','tg-good',pages[0])+lane(right,'🔴 Tidak lolos / ⚪ Belum dinilai','tg-wait',pages[1])+'</div>',unsafe_allow_html=True)
    return accepted,waiting
