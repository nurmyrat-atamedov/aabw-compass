"""Loads the AABW event data (the integration contract).

Everything the agent reasons over lives in data/aabw.json. Organizers can
swap in authoritative data without touching code.
"""
import json
import math
from functools import lru_cache
from pathlib import Path

DATA_PATH = Path(__file__).resolve().parent.parent / "data" / "aabw.json"

# HCMC city-driving assumptions for computing venue-to-venue travel time.
_ROAD_FACTOR = 1.4          # straight-line km -> approx road km
_AVG_KMH = 22.0            # average HCMC traffic speed incl. parking/walking
_MIN_TRAVEL_MIN = 8        # floor: getting between any two rooms/buildings


@lru_cache(maxsize=1)
def load() -> dict:
    with open(DATA_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def reload() -> None:
    """Drop the cached data so the next load() re-reads the file."""
    load.cache_clear()


def save(new_data: dict) -> None:
    """Persist edited event data and hot-reload it (used by the admin panel)."""
    with open(DATA_PATH, "w", encoding="utf-8") as f:
        json.dump(new_data, f, indent=2, ensure_ascii=False)
    load.cache_clear()


def sessions() -> list[dict]:
    return load()["sessions"]


def venues_by_id() -> dict[str, dict]:
    return {v["id"]: v for v in load()["venues"]}


def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def travel_minutes(from_venue: str, to_venue: str) -> int:
    """Computed travel time between venues from their coordinates.

    Haversine distance x road factor, at HCMC average traffic speed. Organizers
    refine accuracy just by setting precise venue lat/lon in aabw.json.
    """
    if from_venue == to_venue:
        return 0
    v = venues_by_id()
    a, b = v.get(from_venue), v.get(to_venue)
    if a and b and "lat" in a and "lat" in b:
        km = _haversine_km(a["lat"], a["lon"], b["lat"], b["lon"]) * _ROAD_FACTOR
        return max(_MIN_TRAVEL_MIN, round(km / _AVG_KMH * 60))
    # fallback if coordinates are missing
    table = load().get("travel_minutes", {})
    return table.get(from_venue, {}).get(to_venue, 25)


def tracks() -> list[dict]:
    return load()["tracks"]


def mentors() -> list[dict]:
    return load().get("mentors", [])


def sponsors() -> list[dict]:
    return load().get("sponsors", [])


def perks() -> list[dict]:
    return load().get("perks", [])


def key_dates() -> list[dict]:
    return load().get("key_dates", [])


def tz_offset() -> str:
    return load()["event"].get("tz_offset", "+07:00")
