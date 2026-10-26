"""Compact monitoring, not a new forecasting model or trading recommendation.

One shared tape supplies the current card and existing checkpoint engine. Historical
frequencies retain their original cutoff; no fusion or uncalibrated probabilities.
"""
import math

import pandas as pd

TZ = 'Asia/Jakarta'


def _number(value):
    try:
        value = float(value)
        return value if math.isfinite(value) else None
    except (TypeError, ValueError):
        return None


def _stamp(value):
    try:
        stamp = pd.Timestamp(value)
        if pd.isna(stamp) or stamp.tzinfo is None:
            return None
        return stamp.tz_convert(TZ)
    except (TypeError, ValueError):
        return None


def opportunity_context(opportunities, ticker, now, latest, session):
    """An unrelated, undated, previous-session or older snapshot cannot confirm tape."""
    out = {'label': 'Tidak ada kandidat Dynamic', 'usable': False}
    if opportunities is None or opportunities.empty or 'ticker' not in opportunities:
        return out
    symbols = opportunities.ticker.astype(str).str.upper().str.removesuffix('.JK')
    rows = opportunities.loc[symbols.eq(ticker)]
    if rows.empty:
        return out
    row = rows.iloc[0]
    generated = _stamp(row.get('generated_at'))
    out['generated_at'] = generated
    if generated is None:
        out['label'] = 'Dynamic belum memiliki waktu yang terverifikasi'
        return out
    import market_session
    snapshot_session = market_session.session_status(now=generated)
    same_session = snapshot_session['status'] == session['status']
    age = (now - generated).total_seconds() / 60
    if (latest is None or generated.date() != now.date() or not 0 <= age <= 5
            or generated < latest or not same_session or not session.get('live')):
        out['label'] = 'Dynamic kedaluwarsa / belum selaras dengan candle terbaru'
        return out
    if str(row.get('candidate_mode', '')).upper() == 'MONITOR_FALLBACK':
        out['label'] = 'Dynamic: MONITOR ONLY'
        return out
    out['usable'] = True
    out['label'] = 'Dynamic: ' + str(row.get('state') or 'WAIT') + ' • ' + str(row.get('entry_status') or 'WAIT')
    # The label describes the upstream snapshot, never upgrades the monitor to BUY.
    return out


def build_monitor(frame, tape, ticker, session, opportunities=None, threshold_pct=.2):
    """Pure view model with strict ticker/day/session boundaries and no future tape."""
    from timemap_data import canonical_ticker, clean, bar_freshness
    ticker = canonical_ticker(ticker)
    now = _stamp(session.get('wib'))
    if now is None:
        raise ValueError('MONITOR REQUIRES AWARE CLOCK')
    view = {'ticker': ticker, 'checked_at': now, 'price': None, 'latest': None,
            'age_min': None, 'direction': 'WAIT', 'movement_pct': None,
            'status': 'WAIT', 'reason': 'Belum ada candle perdagangan hari ini.',
            'data_status': 'DATA UNAVAILABLE', 'source': 'UNAVAILABLE'}
    metadata = dict(tape.attrs) if tape is not None else {}
    try:
        data = clean(tape, ticker, now)
        data.attrs.update(metadata)
    except ValueError:
        data = pd.DataFrame()
        view['reason'] = 'Identitas ticker data tidak cocok; data ditolak.'
    if not data.empty:
        view['source'] = str(metadata.get('source', 'UNKNOWN'))
        view['data_status'] = bar_freshness(data, now)
        latest = data.iloc[-1]
        stamp = _stamp(latest.ts)
        view.update(latest=stamp, price=float(latest.close),
                    age_min=(now - stamp).total_seconds()/60)
        same_day = stamp.date() == now.date()
        from market_session import session_status
        bar_session = session_status(now=stamp)
        same_session = bar_session['status'] == session['status']
        liquid = _number(latest.get('volume')) is not None and float(latest.volume) > 0
        if not session.get('calendar_verified'):
            view['reason'] = 'Kalender belum terverifikasi; hanya pantauan historis.'
        elif not session.get('live'):
            view['reason'] = 'Bursa belum buka, sedang jeda, atau tutup; bukan data live.'
        elif not same_day or not same_session:
            view['reason'] = 'Candle belum berasal dari hari dan sesi yang sedang berjalan.'
        elif metadata.get('fallback') or view['data_status'] in {'DATA STALE', 'DATA UNAVAILABLE'}:
            view['reason'] = 'Provider gagal atau data basi; tunggu data baru.'
        elif view['age_min'] > 5:
            view['reason'] = 'Candle lebih dari 5 menit; belum cukup baru untuk pantauan per menit.'
        elif not liquid:
            view['reason'] = 'Volume transaksi belum cukup untuk membaca pergerakan.'
        else:
            movement = (float(latest.close) / float(latest.open) - 1) * 100
            view.update(movement_pct=movement,
                        direction='UP' if movement > threshold_pct else 'DOWN' if movement < -threshold_pct else 'DATAR',
                        status='PANTAU • NO EDGE',
                        reason='Arah candle berjalan, bukan prediksi berikutnya; feed tetap dapat terlambat.')
    dynamic = opportunity_context(opportunities, ticker, now, view['latest'], session)
    if view['direction'] == 'DOWN':
        view['status'] = 'WAIT'
        view['reason'] = 'Candle berjalan melemah; angka UP historis atau kandidat Dynamic tidak membuktikan pembalikan.'
    if view['status'] == 'WAIT' and dynamic['usable']:
        dynamic['label'] += ' • belum menjadi konfirmasi monitor'
    view['dynamic'] = dynamic

    # Schedule spans the full day. Completed outcomes and future slots differ.
    rows = []
    if frame is not None and 'Data Type' in frame:
        today = frame.loc[frame['Data Type'].eq('TODAY CHECKPOINT')]
        for _, row in today.iterrows():
            if canonical_ticker(row.get('Live Ticker') or ticker) != ticker:
                continue
            state = str(row.get('Checkpoint State', 'WAIT'))
            realized = '—'
            available = _stamp(row.get('Hasil Tersedia WIB'))
            # Legacy checkpoint column is HH:MM, not a timestamp. Bind it to
            # the explicit completed-window timestamp instead of today's clock.
            bar = available - pd.Timedelta(minutes=30) if available is not None else None
            bar_matches = bar is not None and str(row.get('Checkpoint Bar')) == bar.strftime('%H:%M')
            ret = _number(row.get('Slot Return %'))
            valid_outcome = (state == 'REALIZED TODAY' and str(row.get('Market Date')) == now.date().isoformat()
                             and available is not None and available <= now
                             and bar_matches and bar.date() == now.date() and ret is not None)
            if valid_outcome:
                # Recompute direction from this interval, never from cumulative morning score.
                direction = 'UP' if ret > threshold_pct else 'DOWN' if ret < -threshold_pct else 'DATAR'
                realized = f'{direction} {ret:+.2f}%'
                label = 'SUDAH TERJADI'
            elif state in {'BREAK', 'PRE-CLOSING', 'CLOSING', 'CLOSED', 'HOLIDAY', 'WEEKEND', 'NOT TRADING FRIDAY', 'CALENDAR_UNVERIFIED'}:
                label = 'TIDAK BERDAGANG'
            elif state == 'FORMING':
                label = 'SEDANG BERJALAN • NO EDGE'
            else:
                label = 'WAIT • NO EDGE'
            result = {'Waktu WIB': str(row.get('Waktu', '—')), 'Realisasi hari ini': realized}
            probabilities = [_number(row.get(c)) for c in ['Baseline UP %', 'Baseline Sideways %', 'Baseline DOWN %']]
            valid = all(v is not None and 0 <= v <= 100 for v in probabilities)
            if valid:
                valid = abs(sum(probabilities) - 100) <= 1
            for name, value in zip(['UP historis %', 'DATAR historis %', 'DOWN historis %'], probabilities):
                result[name] = f'{value:.2f}' if valid else '—'
            result['Status interval'] = label
            rows.append(result)
    view['schedule'] = pd.DataFrame(rows)
    view['schedule'].attrs['timemap_rendered'] = True
    return view


def render_monitor(history, ticker, session, metadata, opportunities):
    """Render inside the existing 90s fragment; no scan, training or new provider."""
    import streamlit as st
    import timemap_ui_direct_live as layer
    import timemap_checkpoint_today
    from timemap_data import load
    from price_context import timemap_sections
    timemap_checkpoint_today.install()
    augmented = layer._augment(history, st) if not history.empty else history
    tape = load(ticker)  # shared 90s cache, also used by the checkpoint layer
    threshold = float(layer._cfg().get('time_return_threshold', .002)) * 100
    view = build_monitor(augmented, tape, ticker, session, opportunities, threshold)
    st.markdown('### 🕘 Monitor Trading • Time Map')
    day = ('Senin','Selasa','Rabu','Kamis','Jumat','Sabtu','Minggu')[view['checked_at'].dayofweek]
    st.caption(f"{view['ticker']} • {day}, {view['checked_at']:%d-%m-%Y %H:%M:%S} WIB • {session['status']} • otomatis 90 detik")
    price = f"Rp {view['price']:,.0f}" if view['price'] is not None else '—'
    latest = view['latest'].strftime('%d-%m-%Y %H:%M WIB') if view['latest'] is not None else '—'
    age = f"{view['age_min']:.1f} menit" if view['age_min'] is not None else '—'
    st.markdown(f"**{price} · Arah candle: {view['direction']}**")
    st.caption(f"Candle terakhir {latest} • umur dari awal candle {age} • {view['data_status']}. Umur candle bukan ukuran pasti keterlambatan feed.")
    st.info(f"{view['status']} — {view['reason']}")
    st.caption(view['dynamic']['label'])
    st.caption('Peta sampai penutupan: angka UP/DATAR/DOWN adalah frekuensi historis, bukan peluang live. Prediksi jam berikutnya belum tervalidasi; NO EDGE.')
    if not view['schedule'].empty:
        st.dataframe(view['schedule'], hide_index=True, width='stretch')
    else:
        st.info('Jadwal dan sampel Time Map belum tersedia untuk saham ini.')
    with st.expander('Detail data, evaluasi, dan tabel lengkap', expanded=False):
        st.caption(f"Sumber {view['source']} • model R90 statistical baseline • kalender terverifikasi: {bool(session.get('calendar_verified'))}")
        st.caption(f"Historis sampai: {history['historical_asof'].iloc[0] if not history.empty and 'historical_asof' in history else '—'} • dihitung {metadata.get('calculated_at', '—')} • waktu refresh bukan waktu candle atau forecast.")
        st.caption(f"Dynamic snapshot: {view['dynamic'].get('generated_at', '—')} • keselarasan bukan bukti independen atau jaminan arah berikutnya.")
        evaluation = metadata.get('validation', {})
        if evaluation.get('n'):
            st.caption(f"Uji kronologis: {evaluation['n']} hasil • ketepatan {evaluation['accuracy_pct']:.1f}% • Brier {evaluation['brier']:.3f} • {evaluation['status']}. Belum membuktikan keunggulan trading.")
        if evaluation.get('baseline_brier') is not None:
            st.caption(f"Pembanding Brier {evaluation['baseline_brier']:.3f} • {evaluation['evaluation_days']} hari uji • balanced accuracy {evaluation['balanced_accuracy_pct']:.1f}% • galat kalibrasi {evaluation['ece']:.3f}.")
        st.caption(f"Candle baru: {bool(metadata.get('new_candle'))} • pengambilan {metadata.get('elapsed_ms', '—')} ms • forecast dibuat: belum ada model live tervalidasi.")
        st.caption('Sampel arah hanya memakai bar bervolume positif dan horizon 30 menit penuh dalam satu sesi. Jumat sesi II mulai 14.00 WIB. Rencana jam berikutnya tidak menjamin waktu beli atau jual.')
        if metadata.get('errors'):
            st.warning('; '.join(map(str, metadata['errors'])))
        sections = timemap_sections(augmented)
        if sections is not None:
            today, historical = sections
            st.caption('Realisasi hari ini — bukan prediksi')
            st.dataframe(today, hide_index=True, width='stretch')
            st.caption('Statistik historis — BUKAN prediksi live')
            st.dataframe(historical, hide_index=True, width='stretch')
    return view
