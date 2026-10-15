# R34 Single-Service Runtime Host

`runtime_host.py` is an operational fallback when deployment tooling can create a Render web service but cannot create a dedicated Render background worker.

It runs two sibling subprocesses in one container:

- `python worker_base.py` with `AI_ENGINE_WORKER=1`
- `python -m uvicorn runtime_api:app --host 0.0.0.0 --port $PORT`

Safety contract:

- A PostgreSQL `DATABASE_URL`/`NEON_DATABASE_URL` is mandatory. The host exits instead of silently running an ephemeral production worker without persistence.
- If either worker or API exits unexpectedly, the sibling is terminated and the host exits non-zero so Render can restart the complete runtime.
- The existing worker singleton/supervisor remains authoritative inside the child worker.
- The canonical `render.yaml` two-service deployment remains preferred when a real background worker can be provisioned.
- Do not deploy `runtime_host.py` and a separate authoritative worker against the same production database at the same time.

`render.single.yaml` contains the matching one-service blueprint. A non-sleeping Render plan is required for strict 24/7 H-10 operation.
