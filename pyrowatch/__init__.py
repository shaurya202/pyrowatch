"""PyroWatch — satellite fire-detection monitoring backend.

FastAPI + SQLite. Polls NASA FIRMS VIIRS feeds (or generates demo data when
no MAP_KEY is configured), groups detections into fires, enriches with NIFC
WFIGS incidents, NWS weather and EPA AirNow air quality, scores each fire's
relevance to every saved location, and raises alerts.
"""
from __future__ import annotations

__version__ = "1.0.0"
