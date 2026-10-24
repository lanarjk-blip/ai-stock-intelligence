"""Strict official KSEI holiday discovery; failed parsing never replaces a calendar."""
import io
import re
from datetime import date
from html.parser import HTMLParser
from urllib.parse import urljoin, urlparse
import requests

INDEX = 'https://web.ksei.co.id/services/schedule-c-best-instruction'
MONTHS = {'jan':1,'feb':2,'mar':3,'apr':4,'mei':5,'may':5,'jun':6,'jul':7,'agu':8,'agt':8,'aug':8,'sep':9,'okt':10,'oct':10,'nov':11,'des':12,'dec':12}

class Links(HTMLParser):
    def __init__(self):
        super().__init__(); self.links=[]; self.href=None; self.label=''
    def handle_starttag(self,tag,attrs):
        if tag=='a': self.href=dict(attrs).get('href'); self.label=''
    def handle_data(self,data):
        if self.href: self.label+=data
    def handle_endtag(self,tag):
        if tag=='a' and self.href:
            self.links.append((self.href,self.label)); self.href=None

def parse_calendar(text, year):
    if not re.search(r'Bursa\s+Efek\s+Indonesia',text) or not re.search(r'Kalender\s+Libur\s+Bursa\s+Tahun\s+'+str(year),text):
        raise ValueError('Missing IDX calendar reference')
    # Parse only the numbered attachment, never announcement/publication dates.
    table=text.split('Lampiran Pengumuman')[-1]
    rows=re.findall(r'(\d+)\s+(Senin|Selasa|Rabu|Kamis|Jumat)\s+(\d{1,2})-([A-Za-z]+)-(\d{4})',table)
    if not 10 <= len(rows) <= 40 or [int(x[0]) for x in rows] != list(range(1,len(rows)+1)):
        raise ValueError('Incomplete holiday table')
    days=[]
    for _,weekday,d,m,y in rows:
        value=date(int(y),MONTHS[m.lower()[:3]],int(d))
        if value.year != year or value.weekday()!=['Senin','Selasa','Rabu','Kamis','Jumat'].index(weekday):
            raise ValueError('Calendar year/weekday mismatch')
        days.append(value.isoformat())
    if len(set(days))!=len(days) or days!=sorted(days) or f'{year}-01-01' not in days:
        raise ValueError('Calendar ordering/integrity error')
    return days

def fetch_year(year):
    from pypdf import PdfReader
    response=requests.get(INDEX,timeout=(10,15)); response.raise_for_status()
    parser=Links(); parser.feed(response.text)
    candidates=[urljoin(INDEX,h) for h,label in parser.links if str(year) in label and 'Libur' in label and '.pdf' in h.lower()]
    if len(candidates)!=1: raise ValueError('No unambiguous official calendar')
    url=candidates[0]
    if urlparse(url).scheme!='https' or urlparse(url).hostname not in {'web.ksei.co.id','www.ksei.co.id'}:
        raise ValueError('Untrusted calendar host')
    response=requests.get(url,timeout=(10,20),allow_redirects=False); response.raise_for_status()
    if not response.content.startswith(b'%PDF') or len(response.content)>5_000_000: raise ValueError('Invalid calendar PDF')
    text='\n'.join(p.extract_text() for p in PdfReader(io.BytesIO(response.content)).pages)
    return parse_calendar(text,year),url

# A daily background refresh keeps market requests independent of PDF latency.
# The verified result is persisted atomically; bundled data survives disk loss.
import json
import os
import time
from pathlib import Path
from threading import Lock, Thread
_CACHE_PATH = Path(os.environ.get('IDX_CALENDAR_CACHE', 'db/idx_calendar_verified.json'))
_LOCK=Lock()
_LAST_ATTEMPT=0.0
_SNAPSHOT={}

def verified_snapshot():
    global _SNAPSHOT
    if not _SNAPSHOT and _CACHE_PATH.exists():
        try:
            candidate=json.loads(_CACHE_PATH.read_text())
            # Local cache is accepted only with the source URL and valid dates.
            for year,item in candidate.items():
                if urlparse(item['source']).hostname not in {'web.ksei.co.id','www.ksei.co.id'}: raise ValueError('source')
                if not 10<=len(item['days'])<=40: raise ValueError('count')
                if any(date.fromisoformat(d).year!=int(year) for d in item['days']): raise ValueError('year')
            _SNAPSHOT=candidate
        except Exception:
            return {}
    return dict(_SNAPSHOT)

def schedule_refresh(year):
    global _LAST_ATTEMPT
    if os.environ.get('AI_ENGINE_WORKER')=='1': return
    with _LOCK:
        if time.monotonic()-_LAST_ATTEMPT<86400: return
        _LAST_ATTEMPT=time.monotonic()
    def refresh():
        global _SNAPSHOT
        updated=verified_snapshot()
        for y in (year,year+1):
            try:
                days,url=fetch_year(y)
                updated[str(y)]={'days':days,'source':url}
            except Exception:
                continue
        if updated:
            try:
                _CACHE_PATH.parent.mkdir(parents=True,exist_ok=True)
                temporary=_CACHE_PATH.with_suffix('.tmp')
                temporary.write_text(json.dumps(updated,sort_keys=True))
                temporary.replace(_CACHE_PATH)
            except OSError:
                pass
            _SNAPSHOT=updated
    Thread(target=refresh,daemon=True,name='idx-calendar-refresh').start()
