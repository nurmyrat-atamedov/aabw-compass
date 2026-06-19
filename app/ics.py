"""Turn a personalized plan into a .ics calendar.

This is how Compass does proactive reminders with zero notification
infrastructure: the user's own calendar app fires the alerts.
"""
from . import data, planner


def _dt(date: str, time: str) -> str:
    # 2026-07-08 + 14:00 -> 20260708T140000 (floating local time)
    return date.replace("-", "") + "T" + time.replace(":", "") + "00"


def plan_to_ics(profile: dict) -> str:
    plan = planner.build_plan(profile)
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//AABW Compass//EN",
        "CALSCALE:GREGORIAN",
        "X-WR-CALNAME:My AABW Plan",
    ]
    for day in plan["days"]:
        for s in day["sessions"]:
            if s["start"] == s["end"]:
                continue
            why = ", ".join(s["why"]) if s["why"] else ""
            desc = f"{s.get('partner') or ''} | why: {why}".strip(" |")
            lines += [
                "BEGIN:VEVENT",
                f"UID:{s['id']}@aabw-compass",
                f"DTSTART:{_dt(s['date'], s['start'])}",
                f"DTEND:{_dt(s['date'], s['end'])}",
                f"SUMMARY:{s['title']}",
                f"LOCATION:{s['venue']}",
                f"DESCRIPTION:{desc}",
                "BEGIN:VALARM",
                "TRIGGER:-PT20M",
                "ACTION:DISPLAY",
                f"DESCRIPTION:Head to {s['venue']} - {s['title']} starts soon",
                "END:VALARM",
                "END:VEVENT",
            ]
    lines.append("END:VCALENDAR")
    return "\r\n".join(lines)
