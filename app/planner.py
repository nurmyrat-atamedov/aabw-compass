"""The planning engine.

This is the part that makes Compass an agent and not a schedule list: given a
builder's goal, it scores every session, then solves for the optimal
conflict-free, travel-aware day plan (weighted interval scheduling with a
venue-travel feasibility constraint). Pure, deterministic, fast - so the demo
never depends on an LLM being up.
"""
from . import data

# Each goal boosts sessions carrying these tags.
GOAL_TAGS: dict[str, set[str]] = {
    "credits": {"credits", "startup", "scale"},
    "find_team": {"networking", "community", "mentors"},
    "learn_stack": {"learn_ai", "stack", "tooling", "agents", "cloud", "keynote"},
    "meet_investors": {"investors", "networking"},
    "win_track": set(),  # handled specially via the chosen track
}

GOAL_LABELS = {
    "credits": "collect credits/perks",
    "find_team": "find teammates",
    "learn_stack": "learn the stack",
    "meet_investors": "meet investors",
    "win_track": "win your track",
}


def hhmm_to_min(s: str) -> int:
    h, m = s.split(":")
    return int(h) * 60 + int(m)


def _track_tags(track_id: str | None) -> set[str]:
    if not track_id:
        return set()
    for t in data.tracks():
        if t["id"] == track_id:
            return set(t.get("tags", [])) | {t["sponsor"].lower()}
    return set()


def score_session(session: dict, profile: dict) -> tuple[float, list[str]]:
    """Return (score, reasons) for a session given the builder's profile."""
    goals = profile.get("goals", [])
    track_id = profile.get("track")
    interests = set(profile.get("interests", []) or [])
    tags = set(session.get("tags", []))
    score = 0.0
    reasons: list[str] = []

    # CV-derived interests nudge the plan toward what fits the person.
    if interests:
        partner = (session.get("partner") or "").lower()
        if tags & interests or any(i in partner for i in interests):
            score += 1.5
            reasons.append("matches your background")

    for g in goals:
        if g == "win_track":
            ttags = _track_tags(track_id)
            partner = (session.get("partner") or "").lower()
            if tags & ttags or (partner and partner in ttags):
                score += 3
                reasons.append("relevant to your track")
        else:
            hit = tags & GOAL_TAGS.get(g, set())
            if hit:
                score += 2
                reasons.append(GOAL_LABELS.get(g, g))

    # Everyone should see keynotes and not miss deadlines.
    stype = session.get("type")
    if stype in ("keynote", "demo", "ceremony"):
        score += 1.5
    if stype == "deadline":
        score += 100  # never schedule over a deadline marker
        reasons.append("hard deadline")
    if stype == "build":
        score += 5  # the hackathon itself
        reasons.append("the build")

    # Conversational edits: a pinned session is force-kept; the agent pins
    # everything except the slot you're changing, so only that slot moves.
    if session.get("id") in set(profile.get("pinned") or []):
        score += 1000

    # A small base so nothing is exactly zero (keeps ties stable).
    score += 0.1
    return score, list(dict.fromkeys(reasons))


def _solve_day(day_sessions: list[dict]) -> list[dict]:
    """Weighted interval scheduling with travel feasibility.

    Pick the max-score set of non-overlapping sessions such that there is
    enough time to travel between consecutive chosen venues. Classic DP over
    sessions sorted by end time; 'compatible predecessor' must satisfy
    prev.end + travel(prev.venue, cur.venue) <= cur.start.
    """
    items = sorted(day_sessions, key=lambda s: hhmm_to_min(s["end"]))
    n = len(items)
    if n == 0:
        return []

    starts = [hhmm_to_min(s["start"]) for s in items]
    ends = [hhmm_to_min(s["end"]) for s in items]

    def compatible(prev_i: int, cur_i: int) -> bool:
        travel = data.travel_minutes(items[prev_i]["venue"], items[cur_i]["venue"])
        return ends[prev_i] + travel <= starts[cur_i]

    # p[i] = latest index j < i that is compatible to precede i (or -1)
    p = [-1] * n
    for i in range(n):
        for j in range(i - 1, -1, -1):
            if compatible(j, i):
                p[i] = j
                break

    dp = [0.0] * n
    take = [False] * n
    for i in range(n):
        incl = items[i]["_score"] + (dp[p[i]] if p[i] >= 0 else 0.0)
        excl = dp[i - 1] if i > 0 else 0.0
        if incl >= excl:
            dp[i] = incl
            take[i] = True
        else:
            dp[i] = excl
            take[i] = False

    chosen: list[dict] = []
    i = n - 1
    while i >= 0:
        if take[i]:
            chosen.append(items[i])
            i = p[i]
        else:
            i -= 1
    chosen.reverse()
    return chosen


def build_plan(profile: dict) -> dict:
    """Return the personalized, conflict-free plan grouped by day."""
    excluded = set(profile.get("excluded") or [])
    scored: dict[int, list[dict]] = {}
    for s in data.sessions():
        if s["id"] in excluded:
            continue  # conversational edit: user dropped this one
        sc, reasons = score_session(s, profile)
        item = dict(s)
        item["_score"] = sc
        item["why"] = reasons
        scored.setdefault(s["day"], []).append(item)

    venues = data.venues_by_id()
    by_day = []
    for day in sorted(scored.keys()):
        chosen = _solve_day(scored[day])
        # Skip pure logistics/deadline noise unless it's meaningful.
        clean = [
            {
                "id": c["id"],
                "title": c["title"],
                "date": c["date"],
                "start": c["start"],
                "end": c["end"],
                "venue": venues.get(c["venue"], {}).get("name", c["venue"]),
                "venue_id": c["venue"],
                "partner": c.get("partner"),
                "type": c.get("type"),
                "description": c.get("description"),
                "signup": c.get("signup"),
                "tags": c.get("tags", []),
                "why": c["why"],
                "score": round(c["_score"], 2),
            }
            for c in chosen
        ]
        if clean:
            by_day.append({"day": day, "date": clean[0]["date"], "sessions": clean})

    # Freed slots: sessions the user dropped, kept visible so they can re-add.
    by_id = {s["id"]: s for s in data.sessions()}
    removed = []
    for eid in (profile.get("excluded") or []):
        s = by_id.get(eid)
        if not s:
            continue
        removed.append({
            "id": s["id"], "title": s["title"], "day": s["day"], "date": s["date"],
            "start": s["start"], "end": s["end"], "partner": s.get("partner"),
            "venue": venues.get(s["venue"], {}).get("name", s["venue"]),
        })
    return {"profile": profile, "days": by_day, "removed": removed}


def edit_plan(profile: dict, action: str, session_id: str) -> tuple[dict, dict, dict]:
    """Change ONE slot without disturbing the rest.

    Strategy: pin every currently-planned session except the one being changed,
    exclude the target, then re-solve. Because every other pick is pinned and
    the schedule is conflict-free + travel-aware, ONLY the freed time window can
    change, and any replacement must fit that window without overlapping the
    rest. If the schedule has no other session in that window, the slot simply
    opens (a remove, not a swap).

    Returns (new_plan, new_profile_state, info) where info reports what actually
    happened: {"removed", "added": [ids], "filled": bool}.
    """
    current = build_plan(profile)
    before_ids = {s["id"] for d in current["days"] for s in d["sessions"]}
    excluded = list(dict.fromkeys((profile.get("excluded") or []) + [session_id]))
    pinned = list(dict.fromkeys(
        (profile.get("pinned") or []) + [i for i in before_ids if i != session_id]
    ))
    new_profile = {**profile, "pinned": pinned, "excluded": excluded}
    new_plan = build_plan(new_profile)
    new_ids = {s["id"] for d in new_plan["days"] for s in d["sessions"]}
    added = sorted(new_ids - (before_ids - {session_id}))
    info = {"removed": session_id, "added": added, "filled": bool(added)}
    return new_plan, new_profile, info


def add_session(profile: dict, session_id: str) -> tuple[dict, dict, dict]:
    """Re-add a previously removed session and pin it so it stays."""
    excluded = [x for x in (profile.get("excluded") or []) if x != session_id]
    pinned = list(dict.fromkeys((profile.get("pinned") or []) + [session_id]))
    new_profile = {**profile, "excluded": excluded, "pinned": pinned}
    plan = build_plan(new_profile)
    new_ids = {s["id"] for d in plan["days"] for s in d["sessions"]}
    return plan, new_profile, {"added": session_id, "added_ok": session_id in new_ids}


def now_next(profile: dict, now_date: str, now_time: str) -> dict:
    """The lovable core: NOW / NEXT / DON'T-MISS for a given moment.

    now_date 'YYYY-MM-DD', now_time 'HH:MM' in ICT.
    """
    plan = build_plan(profile)
    now_min = hhmm_to_min(now_time)

    today = next((d for d in plan["days"] if d["date"] == now_date), None)
    result = {"now": None, "next": None, "dont_miss": None, "date": now_date, "time": now_time}
    if not today:
        # Before/after the event day: point at the single most important upcoming item.
        upcoming = [s for d in plan["days"] for s in d["sessions"] if d["date"] >= now_date]
        if upcoming:
            best = max(upcoming, key=lambda s: s["score"])
            result["dont_miss"] = best
        return result

    sessions = today["sessions"]
    cur = None
    nxt = None
    for s in sessions:
        if hhmm_to_min(s["start"]) <= now_min < hhmm_to_min(s["end"]):
            cur = s
        elif hhmm_to_min(s["start"]) >= now_min and nxt is None:
            nxt = s

    result["now"] = cur
    if nxt:
        from_venue = cur["venue_id"] if cur else (nxt["venue_id"])
        travel = data.travel_minutes(from_venue, nxt["venue_id"])
        leave_at = hhmm_to_min(nxt["start"]) - travel
        result["next"] = {
            **nxt,
            "travel_min": travel,
            "leave_in_min": max(0, leave_at - now_min),
            "leave_at": f"{leave_at // 60:02d}:{leave_at % 60:02d}",
        }

    # Don't-miss = highest-score thing still ahead today.
    ahead = [s for s in sessions if hhmm_to_min(s["start"]) >= now_min]
    if ahead:
        result["dont_miss"] = max(ahead, key=lambda s: s["score"])
    return result
