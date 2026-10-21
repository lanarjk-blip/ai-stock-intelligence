# AI Stock Intelligence local auto-start

Purpose: make the existing `hybrid_worker.py --daemon` survive device reboot/login without putting Neon credentials in GitHub or adding a paid runtime.

## Secret persistence

Create a local file outside the repository, for example:

```text
DATABASE_URL=<your existing Neon connection string>
```

Never commit this file. Restrict it to the local user (`chmod 600` on Termux/Linux).
`start_hybrid_worker.py` loads it but never prints the value.

## HP / Termux

Prerequisites: the existing repo and Python environment work manually. For boot launch, Android needs a boot integration such as Termux:Boot. Copy `termux/boot-start-ai-stock.sh` to `~/.termux/boot/start-ai-stock.sh`, set `ASI_REPO_DIR` / `ASI_ENV_FILE` locally if paths differ, and make the script executable.

The script waits briefly for Android networking, starts the wrapper with `nohup`, and writes only operational output to `~/.local/state/ai-stock-intelligence/autostart.log`.

Android battery/background restrictions must allow the terminal/boot integration to run; otherwise Android can kill the launcher regardless of Python's watchdog.

## Windows laptop

Import `windows/AI-Stock-Worker.xml` in Task Scheduler after replacing `__REPO_DIR__` and `__ENV_FILE__`. It starts at user logon, uses `StartWhenAvailable`, ignores duplicate task instances, and retries failures. It intentionally runs with least privilege.

## Safety and takeover

Do not manually clear `asi_worker_lease`. The existing Neon distributed lease decides the active writer. The wrapper only starts `hybrid_worker.py --daemon`; it does not bypass singleton, lease, heartbeat, or checkpoint safeguards.

Laptop and HP may both have auto-start enabled. A valid holder keeps the lease; a standby device takes over only when the lease is available/expired according to the existing lease implementation.

## Recovery verification

After enabling auto-start on a device:

1. Start/reboot the device and allow networking.
2. Confirm `hybrid_worker.py --daemon` is running locally.
3. Confirm Neon `asi_worker_lease` gets a current `heartbeat_at` and non-expired `lease_until` for the active holder.
4. Confirm `asi_runtime_heartbeat.heartbeat_at` advances and `worker_generation` changes after a fresh worker start.
5. Confirm fresh collection/intraday timestamps before treating Time Map or live decisions as READY.
6. Reboot once more and repeat. A manual launch that works once is not proof of boot recovery.

If heartbeat does not advance, inspect the local autostart log first. Common blockers are missing env file, missing `DATABASE_URL`, wrong repo path, missing Python dependencies, Android background restrictions, laptop sleep/offline state, or network failure.

## Fail-safe rule

A restored checkpoint is historical recovery material, not proof of live freshness. Until current-session market data arrives, UI/decision layers should remain STALE/LIMITED DATA and must not fabricate a live Time Map.
