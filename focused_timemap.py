"""One selected ticker, one current-data card and one full-day projection table."""
import pandas as pd
import streamlit as st
from timemap_data import load
from market_session import session_status
from trading_monitor import build_monitor,_date_label


def compact_plan(bundle,records,now,cfg=None):
    from timemap_forecast_ui import table,plan_coverage
    from timemap_forecast import schedule
    display=table(bundle,records,now,revision='latest')
    slots=schedule(now,cfg)
    statuses=[]
    for row,slot in zip(display.to_dict('records'),slots):
        if not slot['regular']:status='Jeda / bukan interval 30 menit'
        elif row['Realisasi']!='—':status='Sudah terjadi'
        elif not row['Versi tercatat']:status='Belum ada prediksi tercatat'
        elif slot['end']<=now:status='Menunggu hasil aktual'
        elif slot['start']<now:status='Interval berjalan'
        else:status='Proyeksi uji • belum terpercaya'
        if bundle.get('reason') and row['Versi tercatat'] and row['Realisasi']=='—':
            status='Arsip proyeksi • WAIT'
        statuses.append(status)
    display['Status']=statuses
    short=display[['Interval WIB','Prediksi UP % (uji)','Prediksi DATAR % (uji)',
                   'Prediksi DOWN % (uji)','Realisasi','Status']].rename(columns={
        'Interval WIB':'Jam WIB','Prediksi UP % (uji)':'UP % (uji)',
        'Prediksi DATAR % (uji)':'DATAR % (uji)','Prediksi DOWN % (uji)':'DOWN % (uji)'})
    # Preserve the issued record for audit, but suppress unusable current signals.
    if bundle.get('reason'):
        mask=short['Status'].eq('Arsip proyeksi • WAIT')
        short.loc[mask,['UP % (uji)','DATAR % (uji)','DOWN % (uji)']]='—'
    short.attrs['timemap_rendered']=True
    return short,display,plan_coverage(bundle,records,now,cfg)


def render_plan(bundle,records,now,cfg=None,evidence=None):
    st.markdown('### Proyeksi 09.00 sampai penutupan')
    st.warning('BELUM ADA PREDIKSI TERPERCAYA. Angka berikut adalah proyeksi uji, bukan sinyal beli/jual.')
    short,detail,coverage=compact_plan(bundle,records,now,cfg)
    st.dataframe(short,hide_index=True,width='stretch')
    if bundle.get('reason'):st.info('WAIT — '+bundle['reason'])
    st.caption(f"{coverage['recorded']}/{coverage['expected']} interval tercatat. Slot kosong tidak diisi prediksi susulan setelah waktunya lewat.")
    with st.expander('Detail waktu dan sumber',expanded=False):
        st.caption(f"Tanggal target: {bundle['trading_date']} • Ticker: {bundle['ticker']}")
        st.caption('Data pembentuk model: '+str(bundle.get('training_end') or 'belum cukup')+'; berbeda dari tanggal target.')
        st.caption(f"Dibuat sebelum 09.00 WIB: {coverage['preopen']}/{coverage['expected']} interval. Semua versi asli tetap tersimpan.")
        if evidence is not None:
            st.markdown('**Hasil prediksi asli yang sudah tercatat**')
            if evidence.get('error'):
                st.caption('Evaluasi belum tersedia; tidak dianggap akurasi nol.')
            else:
                st.caption(f"{evidence['n']} hasil selesai • {evidence['days']} hari • {evidence['pending']} menunggu hasil. {evidence['status']}.")
                if evidence['accuracy_pct'] is not None:
                    st.caption(f"Ketepatan tercatat: {evidence['correct']}/{evidence['direction_n']} ({evidence['accuracy_pct']:.2f}%). Ini hasil lampau, bukan peluang trade berikutnya.")
                    st.caption(f"Brier: {evidence['brier']:.4f} (uniform 0.6667; lebih kecil lebih baik). Pemantauan: {evidence['drift']}.")
                if evidence['rejected']:st.caption(f"{evidence['rejected']} interval dikeluarkan karena bukti tidak valid.")
            st.caption('Satu prediksi pertama per interval; revisi yang lebih berhasil tidak menggantikannya. Status tetap NO EDGE.')
        from local_runtime_health import source_fingerprint
        st.caption('Versi pemeriksaan: '+source_fingerprint()[:10])
        for row in detail.to_dict('records'):
            if row['Versi tercatat']:
                st.text(f"{row['Interval WIB']} | dibuat {row['Dibuat WIB']} WIB | input {row['Input tersedia WIB']} WIB | {row['Sumber']}")


def render_selected(ticker,company):
    from timemap_resume import install_resume_guard
    from timemap_forecast_ui import render_forecast
    install_resume_guard()
    @st.fragment(run_every=90)
    def current():
        from local_runtime_health import request_worker_recovery
        request_worker_recovery()
        session=session_status();now=pd.Timestamp(session['wib']).tz_convert('Asia/Jakarta')
        tape=load(ticker)
        view=build_monitor(pd.DataFrame(),tape,ticker,session)
        st.markdown(f'### {ticker} · {company}')
        st.caption(f"{_date_label(now.date())} • diperiksa {now:%H:%M:%S} WIB • otomatis 90 detik")
        price=f"Rp {view['price']:,.0f}" if view['price'] is not None else '—'
        a,b=st.columns(2)
        a.metric('Harga terakhir tersedia',price)
        b.metric('Status data',view['data_status'])
        latest=view['latest']
        if latest is None:
            st.info('WAIT — data pasar belum tersedia.')
        else:
            st.caption(f"Candle asli: {latest:%d-%m-%Y %H:%M:%S} WIB • sumber: {view['source']}")
            if latest.date()!=now.date() or not session.get('live'):
                st.info('Data historis / pasar belum aktif. Bukan harga live hari ini.')
            elif view['data_status']!='LIVE':
                st.caption('Feed dapat terlambat; waktu pemeriksaan bukan waktu transaksi.')
        render_forecast(tape,ticker,session,compact=True)
    current()


def render_focused_app(load_universe,search):
    st.markdown('## ⏱️ Time Map')
    st.caption('Pilih saham • lihat tanggal data • pantau proyeksi setiap 30 menit')
    universe,_=load_universe()
    query=st.text_input('Cari saham',value=str(st.query_params.get('ticker','')),placeholder='Kode atau nama saham',key='focused_stock_query')
    if not query.strip():
        st.info('Masukkan kode atau nama saham untuk membuka Time Map.')
        return
    matches=search(universe,query,8)
    if matches.empty:
        st.info('Saham tidak ditemukan. Periksa kode atau nama perusahaan.')
        return
    labels=[f'{r.ticker} · {r.company}' for r in matches.itertuples()]
    selected=st.selectbox('Saham',labels,key='focused_stock_selection')
    row=matches.iloc[labels.index(selected)]
    ticker=str(row.ticker).upper().removesuffix('.JK')
    st.query_params['ticker']=ticker
    render_selected(ticker,str(row.company))
