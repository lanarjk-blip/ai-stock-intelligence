# R91 Time Map truth integration

Based on main 3355cbcc (#73) and PR #74 d327122e. Keeps #72 ticker isolation and #73 original fetch timestamps.

Implemented: session-scoped cache; explicit delayed/stale/historical/calendar-unverified status; fallback checkpoints cannot be fresh; separate calculation time and no fabricated forecast time; visibility/resume reload guard; official KSEI/IDX 2027 calendar; daily background official calendar discovery with strict table validation, atomic local persistence and bundled fallback. Unknown calendar years disable live sessions.

Official 2027 PDF downloaded and parsed: 20 dates exactly match bundle. Index-page requests timed out in this environment, so end-to-end automatic discovery still requires production verification. Sources can change; failed parsing never replaces a verified calendar.

Model remains R90 Laplace slot-frequency historical baseline. No new live forecast is promoted; no accuracy improvement claimed. Replay against unchanged origin/main estimator matches accuracy/Brier on BBCA (498 bars) and ANTM (499 bars), 2026-08-26 through 2026-10-23, 365 walk-forward outcomes each. See final_accuracy.json. Both fail uniform Brier comparator. These are chronological diagnostics, not an untouched model-selection holdout. Multi-model research is not completed.

Tests: targeted truth/date/session/provider/parser tests and full suite. Last full suite before final additions: 277 passed. Latest CI is authoritative.

Pending external evidence: actual Monday/Tuesday feed arrival, physical Android resume lifecycle, full supported-universe provider coverage, forecast persistence/drift monitoring (no new forecasts produced), and runtime deployment verification. Existing historical bar storage remains unchanged. Local disk is not claimed durable across server replacement; bundle remains versioned and remote calendar can be re-fetched.
