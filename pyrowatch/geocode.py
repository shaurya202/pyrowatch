"""US Census Geocoder — converts a US street address into latitude/longitude (no key required)."""
from __future__ import annotations

import requests

from .config import CENSUS_GEOCODE_URL


class GeocodeError(Exception):
    pass


def geocode(address: str) -> dict:
    """Geocode a one-line address. Returns {'lat','lon','matched','geocoder'}."""
    if not address or not address.strip():
        raise GeocodeError("Address is empty")
    try:
        r = requests.get(
            CENSUS_GEOCODE_URL,
            params={"address": address.strip(), "format": "json", "benchmark": "Public_AR_Current"},
            timeout=20,
            headers={"User-Agent": "PyroWatch/1.0"},
        )
        r.raise_for_status()
        data = r.json()
    except Exception as e:  # network / service errors
        raise GeocodeError(f"Geocoder request failed: {e}") from e

    matches = ((data.get("result") or {}).get("addressMatches")) or []
    if not matches:
        raise GeocodeError(
            "No US address match found. The Census Geocoder only covers the US — "
            'you can paste "lat,lon" coordinates instead.'
        )
    m = matches[0]
    coords = m.get("coordinates") or {}
    lat, lon = coords.get("y"), coords.get("x")
    if lat is None or lon is None:
        raise GeocodeError("Geocoder returned no coordinates")
    return {"lat": float(lat), "lon": float(lon), "matched": m.get("matchedAddress") or address, "geocoder": "US Census Geocoder"}


def suggest_addresses(query: str, limit: int = 5) -> list[dict]:
    """Suggest US addresses matching query. Uses Nominatim with Census fallback."""
    if not query or len(query.strip()) < 2:
        return []

    clean_q = query.strip()

    # Check for direct coordinates "lat, lon"
    parts = clean_q.replace(";", ",").split(",")
    if len(parts) == 2:
        try:
            clat = float(parts[0])
            clon = float(parts[1])
            if abs(clat) <= 90 and abs(clon) <= 180:
                coord_str = f"{clat:.4f}, {clon:.4f}"
                return [{
                    "address": coord_str,
                    "display_name": f"Coordinates: {coord_str}",
                    "name": f"Coords {coord_str}",
                    "city": "",
                    "state": "",
                    "lat": clat,
                    "lon": clon,
                    "type": "coordinates",
                }]
        except ValueError:
            pass

    results: list[dict] = []

    # 1. Try OpenStreetMap Nominatim (handles cities, zip codes, landmarks, partial streets)
    try:
        r = requests.get(
            "https://nominatim.openstreetmap.org/search",
            params={
                "q": clean_q,
                "format": "json",
                "addressdetails": 1,
                "limit": limit,
                "countrycodes": "us",
            },
            headers={"User-Agent": "PyroWatch/1.0 (wildfire-monitor; contact@pyrowatch.local)"},
            timeout=5,
        )
        if r.status_code == 200:
            for item in r.json():
                try:
                    lat = float(item["lat"])
                    lon = float(item["lon"])
                except (ValueError, KeyError):
                    continue
                addr = item.get("address") or {}
                name = item.get("name") or ""
                house = addr.get("house_number", "")
                road = addr.get("road", "")
                city = addr.get("city") or addr.get("town") or addr.get("village") or addr.get("hamlet") or addr.get("suburb") or ""
                state = addr.get("state", "")
                postcode = addr.get("postcode", "")

                street = f"{house} {road}".strip()
                loc_parts = [p for p in [city, state, postcode] if p]
                loc_line = ", ".join(loc_parts)

                if street and loc_line:
                    display = f"{street}, {loc_line}"
                elif name and loc_line and name != city:
                    display = f"{name}, {loc_line}"
                elif loc_line:
                    display = loc_line
                else:
                    display = item.get("display_name", clean_q)

                short_name = name or street or city or clean_q
                results.append({
                    "address": display,
                    "display_name": item.get("display_name", display),
                    "name": short_name,
                    "city": city,
                    "state": state,
                    "lat": lat,
                    "lon": lon,
                    "type": item.get("type", "address"),
                })
    except Exception:
        pass

    # 2. If results is empty, try Census Geocoder (exact/partial street match)
    if not results:
        try:
            r = requests.get(
                CENSUS_GEOCODE_URL,
                params={"address": clean_q, "format": "json", "benchmark": "Public_AR_Current"},
                timeout=5,
                headers={"User-Agent": "PyroWatch/1.0"},
            )
            if r.status_code == 200:
                data = r.json()
                matches = ((data.get("result") or {}).get("addressMatches")) or []
                for m in matches[:limit]:
                    coords = m.get("coordinates") or {}
                    lat, lon = coords.get("y"), coords.get("x")
                    if lat is not None and lon is not None:
                        matched = m.get("matchedAddress") or clean_q
                        results.append({
                            "address": matched,
                            "display_name": matched,
                            "name": matched.split(",")[0].strip(),
                            "city": "",
                            "state": "",
                            "lat": float(lat),
                            "lon": float(lon),
                            "type": "address",
                        })
        except Exception:
            pass

    return results

