"""Shared test setup: import path, environment, and DB isolation."""
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# never start the background poller from inside tests (checked by create_app)
os.environ["PYROWATCH_NO_POLL"] = "1"

import pytest  # noqa: E402

import pyrowatch.db as db  # noqa: E402


@pytest.fixture()
def fresh_db(tmp_path, monkeypatch):
    """Isolated SQLite database for one test (re-points db.DB_PATH into tmp)."""
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "test.db")
    db.init_db()
    return db
