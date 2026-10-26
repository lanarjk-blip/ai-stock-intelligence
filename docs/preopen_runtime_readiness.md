# Pre-open readiness, 27 October 2026

Production browser inspection around 05:35 WIB found persisted Quality READY and
dashboard READY alongside LOCAL_RUNTIME_MISSING in the Pre-Market health panel.
This is not evidence that the live pipeline is healthy. Existing main regression
(419 tests) could pass while this production startup condition remained unmet.

The data-quality reader now checks authoritative runtime health natively;
missing/stale/mismatched heartbeat, engine failure or unavailable health prevents
publication even if Python's startup/UI monkey patches were not installed.
Dashboard/Background Engine status uses the same gate instead of snapshot
existence. A healthy heartbeat never upgrades bad data or an unfinished cycle.

Explicit real-worker bootstrap provides initial heartbeat, one heartbeat thread,
and a source watchdog even without sitecustomize. The supervisor remains owner
of singleton/hybrid leases. The existing sitecustomize threads are reused by name.
Heartbeats retain startup source identity instead of relabeling an old process
with newly edited source hashes. Time Map and screening modules are included in
source fingerprint and reload watch lists.

If Streamlit's launcher lock is occupied but the local worker has no heartbeat
or a build mismatch, one replacement launch is allowed per 180 seconds. Existing
supervisor identity/lock checks decide whether an old process may be replaced.
There is no direct PID killing by the UI, no recovery launch in external mode,
and no restart of healthy/warming workers. Launches explicitly set PYTHONPATH
so normal Python startup can discover sitecustomize.

Targeted verification covers native publication blocking with actual stored
READY state, healthy/bad-data behavior, malformed runtime responses, real UI
card suppression, first heartbeat persistence, no duplicate monitoring threads,
source-change restart, fixed startup identity and bounded recovery. Full CI is
required before merge. No full scan/model retraining was added to page refresh.

Remaining acceptance: observe production runtime after deploy; check Search,
Time Map dates/freshness and history. New live candles and physical Android
cannot be verified before market opens or without an Android device.

Production follow-up: on 27 October at 05:46 WIB the runtime changed to
LOCAL_RUNTIME_READY_REFRESHING with source fingerprint a2a83a0f33, matching the
implemented source; at 05:50:13 WIB it was LOCAL_RUNTIME_READY with latest engine
update 05:46:29 WIB. Search/Ask/Time Map worked for BMRI and BBRI, and stored
recommendations remained accessible in History. BBRI's Time Map check advanced
05:47:46 -> 05:49:16 without a manual refresh; the actual candle remained
26 October 16:00 and WAITING FOR NEW BAR, not live Tuesday data. These are
pre-market browser observations, not physical Android or open-market acceptance.

A subsequent UI check found nested legacy title wrappers repeating the same
status/storage strips. Render-scoped session latches now emit each strip once,
reset at the next dashboard render. This does not bypass access control or
change market analysis. A Streamlit rendering test exercises five nested calls
and a subsequent rerun: one refresh button, one clock/status caption, and one
storage read/caption per run.
