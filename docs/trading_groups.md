# Two-lane ticker screening

The Opportunity tab and the area below Time Map show two side-by-side lists:
Kandidat trading and Belum layak / WAIT. These classify initial operational
setup readiness, not proven profitability, calibrated live edge or an order to
buy. Time Map NO EDGE remains unchanged. No model promotion or APK rebuild.

The entire supported universe is partitioned once. Unknown or conflicting
Dynamic snapshots cannot qualify. Missing evidence is WAIT, not proof that a
stock is inherently unsuitable. Both lists display the actual candle date/time
and a short reason. Search applies literally to all classified symbols, while
at most40 entries per lane are rendered initially to limit mobile page size.
Links open that ticker's existing Deep Analysis.

Candidate gates: verified calendar and live session; known READY quality;
STRICT Dynamic candidate, ENTRY READY, ACTIVE entry window and a nonblocked
BUY/STRONG_BUY/NEAR ENTRY setup; same-day/session candle with a positive price
and volume, known non-cache source and timestamp age0–5minutes; aware Dynamic
snapshot age0–5minutes, generated at or after the candle, and snapshot price
matching the current saved price. Risk must be LOW/MEDIUM and IHSG
BULLISH/SIDEWAYS/NEUTRAL. Positive stop<price<target, current price inside entry
zone, reported R/R>=1.5 and price-based target1 R/R>=1.5 are required. A candle
falling beyond the shared Time Map threshold remains WAIT.

The five-minute age is measured from candle START time. It is a conservative
minute-monitoring eligibility gate, not a feed-latency estimate or guarantee.
Many delayed30-minute bars will correctly fail it. There is no claim that a
candidate meeting these gates has validated predictive accuracy or profits.

The existing90-second Streamlit fragments reread Dynamic snapshots and perform
one read-only query for the latest stored intraday candle per ticker. There are
no new price downloads, universe scans, training jobs, notifications or trades.
The search label avoids the legacy active-ticker tracker so filtering a list
does not change the selected stock's Time Map.

Verification:29 targeted tests passed, including real Streamlit rendering of
both lanes, exact partition, literal search, prior-day/session rollover, future
and naive timestamps, cache/missing volume/falling candles, changed snapshot
price, duplicate identities, missing quality, entry/risk limits, and the actual
stored candle timestamp query. Compile and diff checks passed. A local full
suite attempt was interrupted by network approval cancellation; the existing
GitHub full-regression workflow is required before merge. Live-market and
physical Android behavior require separate observation.
