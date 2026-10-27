"""Append-only shadow forecasts and their later realized outcomes; no trading."""
import json
import sqlite3
from pathlib import Path

import numpy as np
import pandas as pd

from timemap_data import clean,canonical_ticker
from timemap_forecast import VERSION,MODEL_ID,MODEL_SHA256,THRESHOLD,predict
from timemap_statistics import full_regular_bar


DB_PATH=Path(__file__).parent/'db'/'market.db'

def promotion_gate():
    # No automatic promotion from retrospective diagnostics or a UI checkbox.
    return {'approved':False,'status':'MODEL BELUM LOLOS VALIDASI',
            'reason':'Uji historis belum mengungguli baseline; evaluasi prospektif masih diperlukan.'}

def connect(path):
    con=sqlite3.connect(path,timeout=10)
    con.execute('''CREATE TABLE IF NOT EXISTS timemap_forecast_log(
        ticker TEXT NOT NULL, trading_date TEXT NOT NULL, slot TEXT NOT NULL,
        model_version TEXT NOT NULL,input_hash TEXT NOT NULL,generated_at TEXT NOT NULL,
        feature_asof TEXT NOT NULL,training_end TEXT NOT NULL,latest_candle TEXT NOT NULL,
        source TEXT NOT NULL,phase TEXT NOT NULL, probabilities TEXT NOT NULL,samples INTEGER NOT NULL,
        PRIMARY KEY(ticker,trading_date,slot,model_version,input_hash))''')
    con.execute('''CREATE TABLE IF NOT EXISTS timemap_forecast_outcomes(
        ticker TEXT NOT NULL,trading_date TEXT NOT NULL,slot TEXT NOT NULL,
        actual_label INTEGER NOT NULL,return_pct REAL NOT NULL,evaluated_at TEXT NOT NULL,
        source TEXT NOT NULL, PRIMARY KEY(ticker,trading_date,slot))''')
    con.execute('CREATE TABLE IF NOT EXISTS timemap_forecast_watch(ticker TEXT PRIMARY KEY,last_viewed TEXT NOT NULL)')
    return con


def record(path,bundle):
    if bundle.get('model_version')!=MODEL_ID:
        raise ValueError('Model identity mismatch')
    con=connect(path)
    try:
        for row in bundle['rows']:
            if row['p'] is None:continue
            p=np.asarray(row['p'],dtype=float)
            if p.shape!=(3,) or not np.isfinite(p).all() or (p<0).any() or (p>1).any() or not np.isclose(p.sum(),1):
                raise ValueError('Invalid probabilities')
            if (pd.Timestamp(row['start']).date().isoformat()!=bundle['trading_date']
                or pd.Timestamp(bundle['generated_at']).date().isoformat()!=bundle['trading_date']
                or pd.Timestamp(bundle['training_end']).date()>=pd.Timestamp(row['start']).date()):
                raise ValueError('Invalid forecast date boundary')
            if pd.Timestamp(bundle['generated_at'])>pd.Timestamp(row['start']):
                raise ValueError('Cannot record a forecast after interval begins')
            if pd.Timestamp(bundle['feature_asof'])>pd.Timestamp(bundle['generated_at']):
                raise ValueError('Future feature timestamp')
            con.execute('INSERT OR IGNORE INTO timemap_forecast_log VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)',
                (bundle['ticker'],bundle['trading_date'],row['start'],MODEL_ID,bundle['input_hash'],
                 bundle['generated_at'],bundle['feature_asof'],bundle['training_end'],bundle['latest_candle'],
                 bundle['source'],bundle['phase'],json.dumps(row['p']),row['n']))
        con.commit()
    finally:con.close()


def evaluate(path,ticker,tape,now,cfg=None):
    """Never score an unfinished/zero-volume bar or rewrite an existing outcome."""
    ticker=canonical_ticker(ticker);now=pd.Timestamp(now).tz_convert('Asia/Jakarta')
    source=str(tape.attrs.get('source') or 'UNKNOWN')
    if tape.attrs.get('fallback') or 'CACHE' in source.upper() or source.upper() in {'UNKNOWN','UNAVAILABLE'}:return
    d=clean(tape,ticker,now)
    con=connect(path)
    try:
        slots=con.execute('SELECT DISTINCT trading_date,slot FROM timemap_forecast_log WHERE ticker=? AND julianday(generated_at)<=julianday(slot)',(ticker,)).fetchall()
        for day,slot in slots:
            start=pd.Timestamp(slot)
            if (start+pd.Timedelta(minutes=30)>now or d.empty
                or start+pd.Timedelta(minutes=30)>d.ts.max() or not full_regular_bar(start,cfg)):continue
            bars=d[d.ts.eq(start.tz_convert('UTC'))]
            if bars.empty or pd.isna(bars.iloc[-1].volume) or float(bars.iloc[-1].volume)<=0:continue
            bar=bars.iloc[-1];ret=float(bar.close/bar.open-1)
            label=2 if ret>THRESHOLD else 0 if ret < -THRESHOLD else 1
            con.execute('INSERT OR IGNORE INTO timemap_forecast_outcomes VALUES(?,?,?,?,?,?,?)',
                        (ticker,day,slot,label,ret*100,now.isoformat(),source))
        con.commit()
    finally:con.close()


def saved(path,ticker,day):
    con=connect(path)
    try:
        con.row_factory=sqlite3.Row
        rows=con.execute('''SELECT f.*,o.actual_label,o.return_pct FROM timemap_forecast_log f
            LEFT JOIN timemap_forecast_outcomes o USING(ticker,trading_date,slot)
            WHERE f.ticker=? AND f.trading_date=? AND f.model_version=?
            ORDER BY julianday(f.generated_at),f.input_hash''',(canonical_ticker(ticker),day,MODEL_ID)).fetchall()
        return [dict(r) for r in rows]
    finally:con.close()


def prospective_summary(rows):
    # One first-issued prediction per interval, never the best later update.
    first={}
    for row in rows:first.setdefault(row['slot'],row)
    scored=[r for r in first.values() if r.get('actual_label') is not None]
    if not scored:return {'recorded':len(first),'n':0,'accuracy_pct':None,'brier':None}
    p=np.asarray([json.loads(r['probabilities']) for r in scored]);y=np.asarray([r['actual_label'] for r in scored])
    return {'recorded':len(first),'n':len(scored),'accuracy_pct':float((p.argmax(axis=1)==y).mean()*100),
            'brier':float(((p-np.eye(3)[y])**2).sum(axis=1).mean())}


def percentages(p):
    values=np.asarray(p)*10000
    units=np.floor(values).astype(int)
    for i in np.argsort(-(values-units),kind='stable')[:10000-int(units.sum())]:units[i]+=1
    return [f'{v/100:.2f}' for v in units]


def table(bundle,records,now):
    now=pd.Timestamp(now).tz_convert('Asia/Jakarta');latest={}
    for item in records:latest[item['slot']]=item
    rows=[]
    for row in bundle['rows']:
        start=pd.Timestamp(row['start']);end=pd.Timestamp(row['end']);prior=latest.get(row['start'])
        p=None;issued='—';status=row['status'];realized='—';source='—';cutoff='—'
        if prior:
            p=json.loads(prior['probabilities']);issued=pd.Timestamp(prior['generated_at']).tz_convert('Asia/Jakarta').strftime('%d-%m %H:%M:%S')
            source=prior['source'];cutoff=pd.Timestamp(prior['feature_asof']).tz_convert('Asia/Jakarta').strftime('%d-%m %H:%M')
            if prior.get('actual_label') is not None:
                realized=f"{['DOWN','DATAR','UP'][prior['actual_label']]} {prior['return_pct']:+.2f}%"
                status='SUDAH TERJADI • PREDIKSI ASLI TERSIMPAN'
            elif start<now:status='SEDANG BERJALAN / MENUNGGU HASIL'
            else:status=prior['phase']+' • NO EDGE'
            if bundle.get('reason'):status+=' • '+bundle['reason']
        down,flat,up=percentages(p) if p is not None else ['—']*3
        rows.append({'Interval WIB':f'{start:%H:%M}–{end:%H:%M}' if start!=end else f'{start:%H:%M} PENUTUPAN',
                     'Prediksi UP % (uji)':up,'Prediksi DATAR % (uji)':flat,'Prediksi DOWN % (uji)':down,
                     'Dibuat WIB':issued,'Realisasi':realized,'Status':status,
                     'Input tersedia WIB':cutoff,'Sampel latih':prior['samples'] if prior else 0,'Sumber':source})
    return pd.DataFrame(rows)


def render_forecast(tape,ticker,session):
    import streamlit as st
    from trading_monitor import _date_label
    now=pd.Timestamp(session['wib']).tz_convert('Asia/Jakarta')
    path=str(DB_PATH)
    st.markdown('### 🗓️ Proyeksi hari ini sampai penutupan • UJI BAYANGAN')
    st.caption(f"{ticker} • {_date_label(now.date())} • horizon setiap interval reguler 30 menit • {VERSION}")
    st.warning('EKSPERIMENTAL • NO EDGE. Angka adalah proyeksi model bersyarat, belum terbukti akurat atau menguntungkan dan tidak menjadi sinyal BUY. Baris bertanda — berarti dasar datanya belum cukup.')
    # Reuse saved prior-day history without treating stored current-day bars as
    # newly received provider data. The selected ticker alone is queried.
    con=None
    try:
        con=sqlite3.connect(path,timeout=5)
        stored=pd.read_sql_query('SELECT ts,open,high,low,close,volume FROM intraday WHERE ticker=? AND julianday(ts)>=julianday(?) AND julianday(ts)<julianday(?)',
            con,params=(ticker,(now-pd.Timedelta(days=59)).isoformat(),now.normalize().isoformat()))
    except (sqlite3.Error,pd.errors.DatabaseError):
        stored=pd.DataFrame()
    finally:
        if con is not None:con.close()
    combined=pd.concat([stored,tape],ignore_index=True) if not stored.empty else tape.copy()
    combined.attrs=dict(tape.attrs)
    from timemap_ui_direct_live import _cfg
    cfg=_cfg()
    bundle=predict(combined,ticker,now,cfg)
    watch=connect(path)
    try:
        watch.execute('INSERT OR REPLACE INTO timemap_forecast_watch VALUES(?,?)',(canonical_ticker(ticker),now.isoformat()))
        watch.commit()
    finally:watch.close()
    try:
        record(path,bundle)
        evaluate(path,ticker,tape,now,cfg)
        records=saved(path,ticker,now.date().isoformat())
    except (sqlite3.Error,ValueError) as exc:
        st.error('Proyeksi ditahan: pencatatan prediksi belum dapat diverifikasi ('+type(exc).__name__+').')
        return
    st.caption('Prediksi lengkap dapat dibuat sebelum pembukaan jika histori cukup. Pembaruan memakai candle 30 menit yang sudah selesai dan harga pembukaan yang tersedia; refresh layar tidak otomatis mengubah probabilitas atau waktu prediksi. Angka lama tidak ditimpa setelah hasil diketahui.')
    if bundle['reason']:st.info('WAIT — '+bundle['reason'])
    gate=promotion_gate()
    st.info(gate['status']+' — '+gate['reason'])
    try:
        diagnostic=json.loads((Path(__file__).parent/'validation/day_forecast/results.json').read_text())
        if diagnostic.get('model_sha256')==MODEL_SHA256:
            st.caption(f"Uji arsip {len([r for r in diagnostic['coverage'] if r['included']])} ticker, "
                       f"{diagnostic['test_dates'][0]} s.d. {diagnostic['test_dates'][-1]}: "
                       f"ketepatan model {diagnostic['model']['directional_accuracy']*100:.2f}% vs R90 {diagnostic['slot_baseline']['directional_accuracy']*100:.2f}%. "
                       f"{diagnostic['forecast_instances']} proyeksi atas {diagnostic['unique_outcomes']} interval unik; bukan pengujian prospektif dan bukan peluang untung.")
    except (OSError,ValueError,KeyError):
        st.caption('Laporan uji arsip versi ini belum tersedia; model tetap tidak dipromosikan.')
    research=st.checkbox('Lihat angka proyeksi uji bayangan — belum layak sebagai sinyal trading',
                         value=False,key='shadow_forecast_'+ticker)
    display=table(bundle,records,now)
    if not research and not gate['approved']:
        for column in ['Prediksi UP % (uji)','Prediksi DATAR % (uji)','Prediksi DOWN % (uji)']:
            display[column]='—'
        pending=display.Realisasi.eq('—')
        display.loc[pending,'Status']='WAIT • MODEL BELUM LOLOS VALIDASI'
    st.dataframe(display,hide_index=True,width='stretch')
    summary=prospective_summary(records)
    detail=f"Prediksi awal tercatat {summary['recorded']} interval • hasil terselesaikan {summary['n']}"
    if summary['n']:detail+=f" • ketepatan {summary['accuracy_pct']:.1f}% • Brier {summary['brier']:.3f}"
    st.caption(detail+' • evaluasi awal ini belum membuktikan keunggulan trading.')
    if bundle.get('feature_asof'):
        st.caption(f"Input model tersedia sampai {bundle['feature_asof']} • training historis sampai {bundle['training_end']} • sumber {bundle['source']}. Waktu prediksi asli ada pada setiap baris.")


def prepare_watched(path,now,cfg=None):
    """Bounded pre-open preparation for recently viewed tickers, no downloads."""
    from market_session import session_status
    now=pd.Timestamp(now).tz_convert('Asia/Jakarta')
    if session_status(cfg,now=now)['status']!='PRE_MARKET':return {'checked':0,'recorded':0}
    con=connect(path)
    try:
        watched=con.execute('SELECT ticker FROM timemap_forecast_watch WHERE julianday(last_viewed)>=julianday(?) AND julianday(last_viewed)<=julianday(?) ORDER BY julianday(last_viewed) DESC LIMIT 20',
                            ((now-pd.Timedelta(days=7)).isoformat(),now.isoformat())).fetchall()
        count=0
        for (ticker,) in watched:
            try:
                frame=pd.read_sql_query('SELECT ts,open,high,low,close,volume,source FROM intraday WHERE ticker=? AND julianday(ts)>=julianday(?) AND julianday(ts)<julianday(?) ORDER BY julianday(ts)',
                    con,params=(ticker,(now-pd.Timedelta(days=59)).isoformat(),now.isoformat()))
                if frame.empty:continue
                frame.attrs={'ticker':ticker,'source':str(frame.iloc[-1].source)}
                bundle=predict(frame,ticker,now,cfg)
                record(path,bundle)
                count+=sum(r['p'] is not None for r in bundle['rows'])
                evaluate(path,ticker,frame,now,cfg)
            except (ValueError,sqlite3.Error,pd.errors.DatabaseError):
                continue
        return {'checked':len(watched),'recorded':count}
    finally:con.close()
