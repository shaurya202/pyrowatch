"""Background polling loop."""
from __future__ import annotations

import threading
import traceback
import time

from . import db, pipeline
from .config import POLL_INTERVAL_MINUTES
from .util import now_iso


def _loop() -> None:
    interval = max(60.0, POLL_INTERVAL_MINUTES * 60.0)
    while True:
        try:
            pipeline.run_cycle("scheduler")
        except Exception:
            db.set_meta("cycle", {"status": "error", "finished": now_iso(),
                                  "error": traceback.format_exc()[-800:]})
        time.sleep(interval)


def start_background() -> threading.Thread:
    t = threading.Thread(target=_loop, daemon=True, name="pyrowatch-poller")
    t.start()
    return t
