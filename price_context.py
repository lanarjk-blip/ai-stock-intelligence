"""Pure price comparisons: never substitute first intraday trade for previous close."""
import pandas as pd
import math

def previous_close(prices, trading_date):
    if prices is None or prices.empty or 'date' not in prices or 'close' not in prices:
        return None
    dates = pd.to_datetime(prices['date'], errors='coerce', utc=True).dt.tz_convert('Asia/Jakarta').dt.date
    prior = prices.loc[dates < pd.Timestamp(trading_date).date()].copy()
    prior['_date'] = dates.loc[prior.index]
    prior = prior.sort_values('_date')
    values = pd.to_numeric(prior['close'], errors='coerce')
    values = values[values.gt(0) & values.map(math.isfinite)]
    return float(values.iloc[-1]) if len(values) else None

def change_pct(price, reference):
    try:
        price, reference = float(price), float(reference)
        return (price / reference - 1) * 100 if math.isfinite(price) and math.isfinite(reference) and price > 0 and reference > 0 else None
    except (TypeError, ValueError):
        return None

def timemap_sections(frame):
    """Show clearly labelled historical frequencies beside realized direction."""
    if 'Data Type' not in frame:
        return None
    today = frame.loc[frame['Data Type'].eq('TODAY CHECKPOINT')].copy()
    history = frame.loc[frame['Data Type'].eq('HISTORICAL BASELINE')].copy()
    historical_columns = ['Baseline UP %','Baseline Sideways %','Baseline DOWN %','Samples','UP %','Sideways %','DOWN %','Mean Return %','Confidence %']
    labels = {'Baseline UP %':'UP historis %','Baseline DOWN %':'DOWN historis %','Baseline Sideways %':'DATAR historis %'}
    values = {label: today[source].map(lambda v: f'{float(v):.2f}' if pd.notna(v) else '—') for source,label in labels.items() if source in today}
    today = today.drop(columns=historical_columns, errors='ignore')
    position = list(today.columns).index('Live Direction') + 1 if 'Live Direction' in today else len(today.columns)
    for offset,(label,value) in enumerate(values.items()):
        today.insert(position + offset,label,value)
    keep = ['Waktu','Sesi','Market Date','historical_asof','Live Ticker','Samples','UP %','Sideways %','DOWN %','Mean Return %','Catatan']
    history = history[[c for c in keep if c in history]]
    today.attrs['timemap_rendered'] = history.attrs['timemap_rendered'] = True
    return today, history
