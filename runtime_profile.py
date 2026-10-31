"""Choose the workload before importing any legacy scanner or ML bootstrap."""
import json
from pathlib import Path


def timemap_only(cfg=None):
    if cfg is None:
        try:
            cfg = json.loads((Path(__file__).parent / 'config.json').read_text())
        except (OSError, ValueError):
            cfg = {}
    # A missing/invalid setting must not accidentally restart the full scanner.
    # Explicit legacy mode is retained for rollback and historical experiments.
    return str(cfg.get('runtime_workload', 'timemap')).strip().lower() != 'legacy'
