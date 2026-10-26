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

Verification:32 targeted group tests passed, including real Streamlit rendering of
both lanes, exact partition, literal search, prior-day/session rollover, future
and naive timestamps, cache/missing volume/falling candles, changed snapshot
price, duplicate identities, missing quality, entry/risk limits, and the actual
stored candle timestamp query. Compile and diff checks passed. A local full
suite attempt was interrupted by network approval cancellation; the existing
GitHub full-regression workflow is required before merge. Live-market and
physical Android behavior require separate observation.

The persistence path also retains the original fetched_at and source metadata.
Fallback bars are stored as LAST GOOD CACHE so neither saving nor refreshing
can disguise them as newly fetched provider data. Unknown fallback fetch times
remain absent. Existing provider-recovery tests are included in targeted checks.

## Three-way accounting within two columns

The summary now counts LOLOS SARINGAN, TIDAK LOLOS and BELUM BISA DINILAI.
Each canonical supported ticker belongs to exactly one category. Data/calendar/
session/quality/provenance/identity/snapshot completeness checks run before setup
rejection. A stale HIGH-risk snapshot is unassessed, not proof of a current
rejected setup. Only complete current evidence can lead to setup rejection or
acceptance. Gates are not relaxed to force picks.

Counts use the complete universe, independent of search and pagination. Shares
are percentages of ticker counts, NOT accuracy, confidence, or probability of
profit. Largest-remainder rounding to two decimals sums to 100.00% for a
nonempty universe. Empty universes show three zeros. Closed-market classification
is unassessed and the screen explicitly says live assessment has not started.

The left column contains accepted setups; the right contains rejected and
unassessed tickers with distinct badges and reasons. Both have independent page
selectors (40 entries per page), so no ticker is hidden beyond an initial cap.
An expandable reason/count table explains missing coverage and rejections.

Accuracy of this screened group remains BELUM TERBUKTI. Prior model directional
accuracy is not substituted for the unmeasured performance of the new screen.
There is no claim of reduced loss, profitable selection, or a new model.
Targeted tests cover exclusive three-way partition, exact rounded shares,
missing-data precedence over rejection, closed sessions, empty universe, every
page reachable, and invariant full-universe counts during search. Market-open
and physical Android verification remain separate from CI/browser rendering.

## Invalid input guard audit

Reproduced four failure cases before correction: a negative entry-low could pass;
a candle with high below its close could pass; a NaN decline threshold silently
disabled the decline gate; null universe values became phantom NAN/NONE symbols.
The read-only candle query now includes original high/low. Full positive,
finite OHLC bounds are checked before setup qualification. Nonfinite/negative
thresholds and malformed entry bounds/negative risk figures become unassessed.
Missing/sentinel/non-string symbols are excluded and normalized duplicates count
once. Tests exercise these actual failure cases, the SQLite-to-screen path,
and valid rising/falling evidence to ensure the gates do not simply block all.
No filtering rule was loosened, no model retrained, and no accuracy or reduction
in realized trading losses is claimed. Existing start-of-candle five-minute
freshness policy still limits coverage when the feed is delayed.
