"""Loads the AABW event data (the integration contract).

Everything the agent reasons over lives in data/aabw.json. Organizers can
swap in authoritative data without touching code.
"""
import json
from functools import lru_cache
from pathlib import Path

DATA_PATH = Path(__file__).resolve().parent.parent / "data" / "aabw.json"


@lru_cache(maxsize=1)
def load() -> dict:
    with open(DATA_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def sessions() -> list[dict]:
    return load()["sessions"]


def venues_by_id() -> dict[str, dict]:
    return {v["id"]: v for v in load()["venues"]}


def travel_minutes(from_venue: str, to_venue: str) -> int:
    if from_venue == to_venue:
        return 0
    table = load().get("travel_minutes", {})
    return table.get(from_venue, {}).get(to_venue, 30)


def tracks() -> list[dict]:
    return load()["tracks"]


def mentors() -> list[dict]:
    return load().get("mentors", [])


def perks() -> list[dict]:
    return load().get("perks", [])


def key_dates() -> list[dict]:
    return load().get("key_dates", [])


def tz_offset() -> str:
    return load()["event"].get("tz_offset", "+07:00")
