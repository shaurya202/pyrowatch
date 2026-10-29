"""Alert rules.

Fires (per location):
  * new_group       — a detection group appears inside the alert radius
  * new_satellite   — another satellite starts detecting the same group
  * score_increase  — the location's context score for a group jumps >= threshold
Repeated satellite passes over the same fire are folded into the SAME group,
so users do not get a ping every overpass. score_increase respects a cooldown.
"""
from __future__ import annotations

from . import db
from .config import ALERT_SCORE_COOLDOWN_MIN, ALERT_SCORE_DELTA
from .util import age_hours, now_iso, parse_iso

TYPE_LABELS = {
    "new_group": "New detection group",
    "new_satellite": "New satellite detection",
    "score_increase": "Conditions worsening",
}

_CONF_NAME = {"l": "low", "n": "nominal", "h": "high"}


def _cooled(notes: dict) -> bool:
    last = (notes or {}).get("score_increase")
    if not last:
        return True
    try:
        return age_hours(parse_iso(last)) >= (ALERT_SCORE_COOLDOWN_MIN / 60.0)
    except Exception:
        return True


def build_reason(alert_type: str, threat: dict, loc: dict, extra: dict) -> tuple[str, dict]:
    demo_note = " (demo data)" if threat.get("demo") else ""
    last_iso = threat.get("last_acq_utc")
    age_h = age_hours(parse_iso(last_iso)) if last_iso else None
    dist = (threat.get("distance_miles") or 0.0)
    conf = threat.get("confidence") or "n"
    conf_name = _CONF_NAME.get(conf, conf)
    sats = threat.get("satellites") or []
    wfigs_name = ((threat.get("wfigs") or {}).get("name"))

    if alert_type == "new_group":
        cnt = threat.get("detection_count") or 1
        cnt_str = f"{cnt} heat detection point{'s' if cnt != 1 else ''}"
        head = f"New detection group appeared {dist:.1f} mi from {loc['name']}{demo_note}"
        sub = (f"latest detection {age_h:.1f}h ago, {conf_name} confidence, "
               f"{cnt_str} from {', '.join(sats) or 'satellite'}")
        detail = {
            "detection_time_utc": last_iso,
            "age_hours": round(age_h, 1) if age_h is not None else None,
            "confidence": conf_name,
            "sources": list(sats),
            "detections": cnt,
            "wfigs": wfigs_name,
            "score": threat.get("score"),
            "distance_miles": dist,
        }
        return f"{head} — {sub}", detail

    if alert_type == "new_satellite":
        new_sats = extra["new_sats"]
        head = (f"{', '.join(new_sats)} also detected the same activity {dist:.1f} mi "
                f"from {loc['name']}{demo_note}")
        detail = {
            "detection_time_utc": last_iso,
            "age_hours": round(age_h, 1) if age_h is not None else None,
            "confidence": conf_name,
            "sources": list(sats),
            "new_sources": new_sats,
            "detections": threat.get("detection_count"),
            "wfigs": wfigs_name,
            "score": threat.get("score"),
            "distance_miles": dist,
        }
        return head, detail

    if alert_type == "score_increase":
        prev, cur = extra["prev_score"], extra["score"]
        head = (f"Score rose {prev:.0f} → {cur:.0f} for activity {dist:.1f} mi "
                f"from {loc['name']}{demo_note}")
        detail = {
            "prev_score": prev,
            "score": cur,
            "reasons": threat.get("reasons"),
            "sources": list(sats),
            "detection_time_utc": last_iso,
            "wfigs": wfigs_name,
            "distance_miles": dist,
        }
        return head, detail

    return alert_type, {}


def evaluate_location(loc: dict, threats: list[dict]) -> tuple[list[dict], list[dict]]:
    """Run alert rules for one location. Returns (alert_rows, state_updates)."""
    fired: list[dict] = []
    state_updates: list[dict] = []
    radius = loc.get("radius_miles") or 0

    for threat in threats:
        gid = threat["group_id"]
        state = db.get_group_state(loc["id"], gid)
        sats_seen = set(state.get("sats_seen") or []) if (state and state.get("sats_seen")) else set()
        notes = dict(state.get("notes") or {}) if state and state.get("notes") else {}

        dist = threat.get("distance_miles")
        within = dist is not None and dist <= radius
        sats = threat.get("satellites") or []
        types: list[str] = []
        extra_new_sats: list[str] = []

        was_in_radius = bool(notes.get("in_radius"))
        if within:
            if not was_in_radius:
                types.append("new_group")
                notes["in_radius"] = True
            else:
                new_sats = [s for s in sats if s not in sats_seen]
                if new_sats:
                    extra_new_sats = new_sats
                    types.append("new_satellite")
                prev = state.get("last_score")
                if prev is not None and (threat.get("score") or 0) - prev >= ALERT_SCORE_DELTA and _cooled(notes):
                    types.append("score_increase")
        else:
            notes["in_radius"] = False

        for t in types:
            if t == "new_satellite":
                reason, detail = build_reason(t, threat, loc, {"new_sats": extra_new_sats})
            elif t == "score_increase":
                reason, detail = build_reason(t, threat, loc,
                                              {"prev_score": state.get("last_score"),
                                               "score": threat.get("score")})
            else:
                reason, detail = build_reason(t, threat, loc, {})
            fired.append({
                "location_id": loc["id"],
                "group_id": gid,
                "type": t,
                "fired_utc": now_iso(),
                "score": threat.get("score"),
                "score_prev": state.get("last_score") if state else None,
                "distance_miles": dist,
                "confidence": threat.get("confidence"),
                "sources": list(sats),
                "reason": reason,
                "detail": detail,
            })
            notes[t] = now_iso()

        state_updates.append({
            "location_id": loc["id"],
            "group_id": gid,
            "first_seen_utc": (state or {}).get("first_seen_utc") or now_iso(),
            "last_seen_utc": now_iso(),
            "sats_seen": sorted(sats_seen | set(sats)),
            "last_score": threat.get("score"),
            "notes": notes,
        })
    return fired, state_updates
