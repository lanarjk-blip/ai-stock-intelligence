# Broader ticker and cost-aware diagnostic — 26 October 2026

**NO PROMOTION. No stock in this report is a live recommendation.** Production
model, app signals and APK remain unchanged. This research broadens beyond PTSP
and MPXL and tests idealized trading economics rather than accuracy alone.

## Coverage and immutable candidate

Requested20 other tickers: BMRI, BBNI, ASII, UNTR, INDF, ICBP, KLBF, PGAS, MEDC,
ADRO, ITMG, ADMR, EXCL, ISAT, AMRT, ACES, BRIS, PTBA, JPFA, GOTO. Two workers,
59-day range and8s read timeouts were bounded and stop on429. AMRT and ACES
returned476 raw bars each and40 eligible dates. EXCL/ISAT returned no usable
bars; the other16 have insufficient history. These are retrieval outcomes, not
claims of listing status, liquidity or live production availability.

Additional bounded eight-week recovery for BMRI/ASII/INDF/PGAS returned174,
174,232,232 raw bars; after combining recent snapshots they still have only17,
17,19,19 eligible dates. Missing periods were never padded. Attempts, failures,
provider timestamps and hashes are preserved in collection.json and
recovered/manifest.json.

Before any model scoring, the already frozen ensemble candidate was fixed by
SHA256 (trained through13October on BBCA/ANTM/BBRI/TLKM). No weights, model,
threshold or stock ranking were selected using these new test outcomes. Test
period14–23October comprises8 sessions,152 outcomes,76 per AMRT/ACES. The
baseline uses that ticker's history only through13October. This is an external
retrospective ticker extension, not prospective live execution evidence.

The initial collection protocol hash remains in collection.json. Its amendment
(weekly recovery and deterministic maximum-one-ticker ranking) was recorded
after coverage counts, before candidate scoring or simulated trade returns.
The amendment and original hash are retained in protocol.json; no false claim
of an unchanged registration is made.

## Direction results

| Dataset | Frozen ensemble correct | Same-sample clock-slot baseline |
|---|---:|---:|
| AMRT,76 outcomes |39.47%|38.16%|
| ACES,76 outcomes |30.26%|38.16%|
| Combined,152 outcomes |34.87%|38.16%|

Combined balanced accuracy35.86% versus baseline41.30%; UP precision28.57%,
DOWN31.67%, Brier0.674384 versus0.689953, ECE0.09403 versus0.06132.
A lower Brier alone does not rescue poorer classification or trading results.
The earlier41.45% on four other tickers was not a universal accuracy estimate.

## Fixed paper-trade simulation

Candidate rule fixed before scoring: UP probability>=0.50, UP-DOWN>=0.15,
prior volume ratio>=0.8 and feature gap<=0.5hours. At most one ticker per candle,
ranked by UP-DOWN, alphabetical tie break. Realized profit never selects stocks.
Ideal entry at30-minute candle open and exit at its close; long only, no
simultaneous positions. These fills, liquidity and execution are not verified.
No stop/target behavior, lot sizing, capital deployment or real broker orders.

| Assumed round-trip fees + slippage | Trades | Positive after costs | Mean net return per trade |
|---|---:|---:|---:|
|0.30% +0.10%|4|1/4| -0.897% |
|0.50% +0.10%|4|1/4| -1.097% |

The four trades span only three dates: far too few to estimate an expected live
win rate. Gross mean was already-0.497%; costs make it worse. Fees/slippage are
explicit scenarios, not claims about a broker's actual rates. The slot baseline
produced zero qualifying trades; no win-rate is fabricated for that empty set.
No guaranteed profit, percentage improvement or recommendation is inferred.

## Engineering checks and continuation

Six behavioral tests cover future-return-independent ranking, one position per
bar, cost-induced losses, honest no-trade results, feature-time chronology,
coverage and source/candidate identity. The candidate remains checksum-fixed;
no retraining was needed. Complete regression runs in GitHub before merge.

Reproduce scored artifacts without new network requests:

```
OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 python validation/broad_tickers/evaluate.py
python -m pytest -q tests/test_broad_ticker_evaluation.py
```

For explicit bounded weekly recovery only:
`python validation/broad_tickers/collect.py --recover`.

Remaining blockers: complete verifiable intraday history for the16 incomplete
tickers, provider resolution for2 empty results, genuinely prospective forecasts
recorded before outcomes, and executable risk/cost evidence. A new candidate
must be developed and validated without reusing these external scores to claim
an untouched holdout. Paid-feed trial approval/credentials remain unavailable.
