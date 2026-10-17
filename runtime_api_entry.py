"""Runtime API entrypoint with authentication routes installed."""
from __future__ import annotations

import runtime_api
import auth_api
import auth_bootstrap

app = runtime_api.app
auth_bootstrap.bootstrap_from_env()
auth_api.install(app)
