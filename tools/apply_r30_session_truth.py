from pathlib import Path

path = Path('app.py')
src = path.read_text(encoding='utf-8')

import_anchor = 'import ai_stock_ask\n'
if import_anchor not in src:
    raise SystemExit('ai_stock_ask import anchor missing')
if 'import market_session\n' not in src:
    src = src.replace(import_anchor, import_anchor + 'import market_session\n', 1)

start = src.index('def intraday_checkpoint_status():')
end = src.index('\ndef stock_search_panel(', start)
new_func = '''def intraday_checkpoint_status(now=None):
    """Return the UI checkpoint from the same authoritative IDX session guard as the engine."""
    session=market_session.session_status(CFGD,now=now)
    status=str(session.get('status') or 'CLOSED').upper()
    try:
        local=datetime.fromisoformat(str(session.get('wib')))
    except Exception:
        local=datetime.now(ZoneInfo('Asia/Jakarta'))
    hm=local.hour*60+local.minute
    wd=local.weekday()

    def hhmm(minutes):
        return f'{int(minutes)//60:02d}:{int(minutes)%60:02d}'

    def configured_minutes(key, fallback):
        raw=str(CFGD.get(key) or fallback)
        try:
            h,m=raw.split(':',1)
            return int(h)*60+int(m)
        except Exception:
            h,m=fallback.split(':',1)
            return int(h)*60+int(m)

    if status=='WEEKEND':
        next_day=market_session.next_trading_date(CFGD,now=local,after_close=True)
        return 'WEEKEND',f'{next_day} 09:00','Tidak ada sesi reguler'
    if status=='HOLIDAY':
        next_day=market_session.next_trading_date(CFGD,now=local,after_close=True)
        return 'HOLIDAY',f'{next_day} 09:00','Libur bursa IDX'
    if status=='PRE_MARKET':
        return 'PRE-MARKET','09:00','Menunggu pembukaan sesi reguler'
    if status=='BREAK':
        start_key='idx_fri_session2_open' if wd==4 else 'idx_session2_open'
        start_fallback='14:00' if wd==4 else '13:30'
        return 'BREAK',hhmm(configured_minutes(start_key,start_fallback)),'Istirahat pasar reguler'
    if status=='SESSION_1':
        close_key='idx_fri_session1_close' if wd==4 else 'idx_session1_close'
        close_fallback='11:30' if wd==4 else '12:00'
        close_min=configured_minutes(close_key,close_fallback)
        checkpoints=[9*60,9*60+30,10*60,10*60+30,11*60]
        if close_min>=11*60+30:
            checkpoints.append(11*60+30)
        if close_min>=12*60:
            checkpoints.append(12*60)
        prev=max([x for x in checkpoints if x<=hm],default=9*60)
        nxt=next((x for x in checkpoints if x>hm),close_min)
        return 'SESI I',hhmm(nxt),f'Checkpoint aktif {hhmm(prev)}'
    if status=='SESSION_2':
        open_key='idx_fri_session2_open' if wd==4 else 'idx_session2_open'
        open_fallback='14:00' if wd==4 else '13:30'
        close_key='idx_fri_session2_close' if wd==4 else 'idx_session2_close'
        close_fallback='15:50'
        open_min=configured_minutes(open_key,open_fallback)
        close_min=configured_minutes(close_key,close_fallback)
        checkpoints=[open_min]
        cursor=((open_min+29)//30)*30
        while cursor<close_min:
            if cursor not in checkpoints:
                checkpoints.append(cursor)
            cursor+=30
        checkpoints=sorted(set(checkpoints))
        prev=max([x for x in checkpoints if x<=hm],default=open_min)
        nxt=next((x for x in checkpoints if x>hm),close_min)
        return 'SESI II',hhmm(nxt),f'Checkpoint aktif {hhmm(prev)}'

    # Authoritative regular market is closed. Do not label auction/post-trading
    # periods as a live regular session because the actionable engine is closed.
    next_day=market_session.next_trading_date(CFGD,now=local,after_close=True)
    return 'AFTER-MARKET',f'{next_day} 09:00','Sesi reguler sudah tutup'
'''
src = src[:start] + new_func + src[end:]

old_open = "    market_open=session_name in ['SESI I','SESI II','PRE-CLOSING','POST-TRADING']\n"
new_open = "    market_open=session_name in ['SESI I','SESI II']\n"
if old_open not in src:
    raise SystemExit('market_open UI anchor missing')
src = src.replace(old_open, new_open, 1)

path.write_text(src, encoding='utf-8')
print('R30 SESSION TRUTH PATCH APPLIED')
