"""SQLite storage (WAL). One connection per operation to stay thread-safe."""
from __future__ import annotations

import sqlite3
from typing import Any, Iterable

from .config import DB_PATH
from .util import jdump, jload

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (
  key TEXT PRIMARY KEY, value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS locations (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL, address TEXT, lat REAL NOT NULL, lon REAL NOT NULL,
  radius_miles INTEGER NOT NULL DEFAULT 15,
  aqi TEXT, created_utc TEXT NOT NULL, updated_utc TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS detections (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  satellite TEXT NOT NULL, source TEXT NOT NULL, demo INTEGER NOT NULL DEFAULT 0,
  lat REAL NOT NULL, lon REAL NOT NULL, acq_utc TEXT NOT NULL,
  confidence TEXT NOT NULL DEFAULT 'n', frp REAL, daynight TEXT, fetched_utc TEXT,
  group_id INTEGER,
  UNIQUE (satellite, acq_utc, lat, lon)
);
CREATE INDEX IF NOT EXISTS idx_det_group ON detections(group_id);
CREATE INDEX IF NOT EXISTS idx_det_acq ON detections(acq_utc);
CREATE TABLE IF NOT EXISTS groups (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  center_lat REAL NOT NULL, center_lon REAL NOT NULL,
  first_acq_utc TEXT NOT NULL, last_acq_utc TEXT NOT NULL,
  detection_count INTEGER NOT NULL DEFAULT 1,
  confidence TEXT NOT NULL DEFAULT 'n', frp REAL,
  satellites TEXT NOT NULL DEFAULT '[]',
  wfigs TEXT, weather TEXT,
  demo INTEGER NOT NULL DEFAULT 0, status TEXT NOT NULL DEFAULT 'active',
  created_utc TEXT, updated_utc TEXT
);
CREATE INDEX IF NOT EXISTS idx_groups_last ON groups(last_acq_utc);
CREATE TABLE IF NOT EXISTS snapshots (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  location_id INTEGER NOT NULL, group_id INTEGER NOT NULL, ts_utc TEXT NOT NULL,
  score REAL NOT NULL, reasons TEXT, limited TEXT, distance_miles REAL
);
CREATE INDEX IF NOT EXISTS idx_snap_loc ON snapshots(location_id, ts_utc);
CREATE INDEX IF NOT EXISTS idx_snap_loc_group ON snapshots(location_id, group_id, ts_utc);
CREATE TABLE IF NOT EXISTS group_states (
  location_id INTEGER NOT NULL, group_id INTEGER NOT NULL,
  first_seen_utc TEXT, last_seen_utc TEXT,
  sats_seen TEXT NOT NULL DEFAULT '[]',
  last_score REAL, notes TEXT,
  PRIMARY KEY (location_id, group_id)
);
CREATE TABLE IF NOT EXISTS alerts (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  location_id INTEGER NOT NULL, group_id INTEGER NOT NULL,
  type TEXT NOT NULL, fired_utc TEXT NOT NULL,
  score REAL, score_prev REAL, distance_miles REAL, confidence TEXT,
  sources TEXT, reason TEXT, detail TEXT
);
CREATE INDEX IF NOT EXISTS idx_alerts_time ON alerts(fired_utc DESC);
CREATE TABLE IF NOT EXISTS perimeters (
  irwin TEXT PRIMARY KEY,
  name TEXT, kind TEXT,
  acres REAL, contained_pct REAL,
  ring TEXT NOT NULL, updated_utc TEXT
);
"""


def connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=15)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db() -> None:
    with connect() as c:
        c.executescript(SCHEMA)


def q(sql: str, args: Iterable = (), one: bool = False):
    with connect() as c:
        rows = [dict(r) for r in c.execute(sql, tuple(args)).fetchall()]
    return (rows[0] if rows else None) if one else rows


def x(sql: str, args: Iterable = ()) -> int:
    with connect() as c:
        cur = c.execute(sql, tuple(args))
        c.commit()
        return cur.rowcount


def xm(statements: list[tuple[str, tuple]]) -> None:
    with connect() as c:
        for sql, args in statements:
            c.execute(sql, args)
        c.commit()


# ---------------------------------------------------------------------------

def set_meta(key: str, value: dict) -> None:
    x("INSERT INTO meta(key, value) VALUES(?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
      (key, jdump(value)))


def get_meta(key: str, default=None) -> Any:
    row = q("SELECT value FROM meta WHERE key=?", (key,), one=True)
    return jload(row["value"], default) if row else default


# -- locations ---------------------------------------------------------------

def add_location(name: str, address: str, lat: float, lon: float, radius_miles: int) -> int:
    from .util import now_iso
    with connect() as c:
        cur = c.execute(
            "INSERT INTO locations(name, address, lat, lon, radius_miles, created_utc, updated_utc) "
            "VALUES(?,?,?,?,?,?,?)",
            (name.strip(), address, lat, lon, int(radius_miles), now_iso(), now_iso()))
        c.commit()
        return int(cur.lastrowid)


def update_location(loc_id: int, **fields) -> None:
    from .util import now_iso
    allowed = {"name", "address", "lat", "lon", "radius_miles", "aqi"}
    sets, args = [], []
    for k, v in fields.items():
        if k not in allowed:
            continue
        if k == "aqi" and not isinstance(v, str):
            v = jdump(v)
        sets.append(f"{k}=?")
        args.append(v)
    if not sets:
        return
    sets.append("updated_utc=?")
    args.append(now_iso())
    args.append(loc_id)
    x(f"UPDATE locations SET {', '.join(sets)} WHERE id=?", args)


def list_locations() -> list[dict]:
    out = q("SELECT * FROM locations ORDER BY id")
    for row in out:
        row["aqi"] = jload(row.get("aqi"), None)
    return out


def delete_location(loc_id: int) -> None:
    with connect() as c:
        c.execute("DELETE FROM locations WHERE id=?", (loc_id,))
        c.execute("DELETE FROM snapshots WHERE location_id=?", (loc_id,))
        c.execute("DELETE FROM group_states WHERE location_id=?", (loc_id,))
        c.execute("DELETE FROM alerts WHERE location_id=?", (loc_id,))
        c.commit()


# -- detections --------------------------------------------------------------

def upsert_detections(rows: list[dict]) -> list[dict]:
    """Insert rows that are not already stored (by composite key); returns new rows only."""
    created: list[dict] = []
    with connect() as c:
        cur = c.cursor()
        for r in rows:
            cur.execute(
                "INSERT OR IGNORE INTO detections(satellite, source, demo, lat, lon, acq_utc, confidence, frp, daynight, fetched_utc)"
                " VALUES(?,?,?,?,?,?,?,?,?,?)",
                (r["satellite"], r["source"], int(r.get("demo", 0)), r["lat"], r["lon"], r["acq_utc"],
                 r.get("confidence", "n"), r.get("frp"), r.get("daynight"), r.get("fetched_utc")))
            if cur.rowcount > 0:
                created.append(r)
        c.commit()
    return created


def detections_for_groups(group_ids: list[int]) -> dict[int, list[dict]]:
    if not group_ids:
        return {}
    marks = ",".join("?" * len(group_ids))
    rows = q(f"SELECT * FROM detections WHERE group_id IN ({marks})", tuple(group_ids))
    out: dict[int, list[dict]] = {}
    for r in rows:
        out.setdefault(r["group_id"] or 0, []).append(r)
    return out


# -- groups ------------------------------------------------------------------

def load_groups() -> list[dict]:
    out = q("SELECT * FROM groups")
    for g in out:
        g["satellites"] = jload(g.get("satellites"), [])
        g["wfigs"] = jload(g.get("wfigs"), None)
        g["weather"] = jload(g.get("weather"), None)
    return out


def save_groups_and_link(groups: list[dict], links: list[tuple] | None = None) -> None:
    """Insert/update groups; then bind newly inserted detection rows to their groups."""
    from .util import now_iso
    now = now_iso()
    with connect() as c:
        cur = c.cursor()
        for g in groups:
            sats = jdump(list(g.get("satellites") or []))
            if not g.get("_new") and g.get("id"):
                cur.execute(
                    "UPDATE groups SET center_lat=?, center_lon=?, first_acq_utc=?, last_acq_utc=?, "
                    "detection_count=?, confidence=?, frp=?, satellites=?, wfigs=?, weather=?, demo=?, "
                    "status=?, updated_utc=? WHERE id=?",
                    (g["center_lat"], g["center_lon"], g["first_acq_utc"], g["last_acq_utc"],
                     g["detection_count"], g["confidence"], g["frp"], sats,
                     jdump(g["wfigs"]) if g.get("wfigs") else None,
                     jdump(g["weather"]) if g.get("weather") else None,
                     int(g.get("demo", 0)), g.get("status", "active"), now, g["id"]))
            elif g.get("_new"):
                cur.execute(
                    "INSERT INTO groups(center_lat, center_lon, first_acq_utc, last_acq_utc, detection_count,"
                    " confidence, frp, satellites, wfigs, weather, demo, status, created_utc, updated_utc)"
                    " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (g["center_lat"], g["center_lon"], g["first_acq_utc"], g["last_acq_utc"],
                     g["detection_count"], g["confidence"], g["frp"], sats,
                     jdump(g["wfigs"]) if g.get("wfigs") else None,
                     jdump(g["weather"]) if g.get("weather") else None,
                     int(g.get("demo", 0)), g.get("status", "active"), now, now))
                g["id"] = cur.lastrowid
                g["_new"] = False
        c.commit()
    if not links:
        return
    with connect() as c:
        for row, g in links:
            if g.get("id"):
                c.execute(
                    "UPDATE detections SET group_id=? WHERE satellite=? AND acq_utc=? AND lat=? AND lon=?",
                    (g["id"], row["satellite"], row["acq_utc"], row["lat"], row["lon"]))
        c.commit()


# -- snapshots / alerts / states ---------------------------------------------

def insert_snapshots(rows: list[dict]) -> None:
    with connect() as c:
        c.executemany(
            "INSERT INTO snapshots(location_id, group_id, ts_utc, score, reasons, limited, distance_miles)"
            " VALUES(?,?,?,?,?,?,?)",
            [(r["location_id"], r["group_id"], r["ts_utc"], r["score"], jdump(r.get("reasons")),
              jdump(r.get("limited")), r.get("distance_miles")) for r in rows])
        c.commit()


def get_group_state(location_id: int, group_id: int) -> dict | None:
    row = q("SELECT * FROM group_states WHERE location_id=? AND group_id=?", (location_id, group_id), one=True)
    if not row:
        return None
    row["sats_seen"] = jload(row.get("sats_seen"), [])
    row["notes"] = jload(row.get("notes"), {}) or {}
    return row


def save_group_state(state: dict) -> None:
    x("INSERT INTO group_states(location_id, group_id, first_seen_utc, last_seen_utc, sats_seen, last_score, notes)"
      " VALUES(?,?,?,?,?,?,?)"
      " ON CONFLICT(location_id, group_id) DO UPDATE SET first_seen_utc=excluded.first_seen_utc,"
      " last_seen_utc=excluded.last_seen_utc, sats_seen=excluded.sats_seen,"
      " last_score=excluded.last_score, notes=excluded.notes",
      (state["location_id"], state["group_id"], state.get("first_seen_utc"),
       state.get("last_seen_utc"), jdump(state.get("sats_seen") or []),
       state.get("last_score"), jdump(state.get("notes") or {})))


def insert_alerts(rows: list[dict]) -> None:
    if not rows:
        return
    with connect() as c:
        c.executemany(
            "INSERT INTO alerts(location_id, group_id, type, fired_utc, score, score_prev,"
            " distance_miles, confidence, sources, reason, detail) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            [(r["location_id"], r["group_id"], r["type"], r["fired_utc"], r.get("score"),
              r.get("score_prev"), r.get("distance_miles"), r.get("confidence"),
              jdump(r.get("sources") or []), r.get("reason"), jdump(r.get("detail") or {})) for r in rows])
        c.commit()


def save_perimeters(rows: list[dict]) -> None:
    x("DELETE FROM perimeters")
    insert_rows = [(str(r["irwin"]), r["name"], r["kind"], r["acres"], r["contained_pct"],
                    jdump(r["ring"]), r["updated_utc"]) for r in rows]
    if insert_rows:
        with connect() as c:
            c.executemany(
                "INSERT OR REPLACE INTO perimeters(irwin, name, kind, acres, contained_pct, ring,"
                " updated_utc) VALUES(?,?,?,?,?,?,?)", insert_rows)
            c.commit()


def list_perimeters() -> list[dict]:
    out = q("SELECT * FROM perimeters")
    for p in out:
        p["ring"] = jload(p.get("ring"), [])
    return out


# -- maintenance -------------------------------------------------------------

def prune(older_days: int = 7) -> None:
    from .util import iso, utcnow
    import datetime as _dt
    cutoff = iso(utcnow() - _dt.timedelta(days=older_days))
    x("DELETE FROM snapshots WHERE ts_utc < ?", (cutoff,))
    x("DELETE FROM detections WHERE acq_utc < ?", (iso(utcnow() - _dt.timedelta(days=10)),))
