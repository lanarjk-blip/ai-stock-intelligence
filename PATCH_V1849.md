# V1.8.49 worker reliability fix

Observed production log on 2026-10-14:
- full collection: 318/957 successful, 639 errors
- repeated 404 provider symbols
- IHSG can be empty on a later refresh
- universe seed file may be absent in Streamlit runtime
- runtime log reported engine 1.8.40 while repository config is newer

Planned fix:
1. Route full collector through the existing registry-aware `fetch_chart_recover()` path instead of raw `fetch_chart()`.
2. Treat missing optional seed CSV as a normal fallback, not an error.
3. Preserve last stored IHSG context when a transient provider refresh is empty.
4. Verify runtime version after deployment/restart.

Do not change JST thresholds until collection reliability is verified.