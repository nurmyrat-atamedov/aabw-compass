"""The agent core.

Compass is agent-driven: an Amazon Bedrock model (Claude, via the Converse API
+ tool use) runs a multi-turn loop. It decides which tools to call, fetches
real event data through them, observes the results, and either calls another
tool or answers. Every turn is recorded as a visible trace so you can see the
agent think -> act -> observe -> answer.

The deterministic scheduler (planner.build_plan) is one of the agent's tools.
That is intentional: good agents orchestrate reliable tools rather than
hallucinating structured output. The agent decides *what* matters for this
builder; the tool computes the conflict-free schedule.

If Bedrock is unavailable (no AWS creds), a deterministic pipeline runs the
same tools in a sensible order and emits the same trace shape, so the product
never breaks. The response is always labelled `bedrock` or `local` so it is
transparent which brain answered.
"""
import os

from . import data, planner

# ---- Tools: the only actions the agent can take -----------------------------


def tool_build_plan(profile: dict) -> dict:
    return planner.build_plan(profile)


def tool_now_next(profile: dict, date: str, time: str) -> dict:
    return planner.now_next(profile, date, time)


def tool_search_sessions(query: str) -> list[dict]:
    q = (query or "").lower()
    out = []
    for s in data.sessions():
        hay = " ".join([s["title"], s.get("partner") or "",
                        " ".join(s.get("tags", [])), s.get("description") or ""]).lower()
        if not q or any(w in hay for w in q.split()):
            out.append({"title": s["title"], "day": s["day"], "start": s["start"],
                        "venue": s["venue"], "partner": s.get("partner")})
    return out[:6]


def tool_find_mentors(topic: str) -> list[dict]:
    q = (topic or "").lower()
    out = [m for m in data.mentors()
           if any(w in " ".join([m["name"], m["role"], m["org"], " ".join(m.get("tags", []))]).lower()
                  for w in q.split())]
    return out[:5] or data.mentors()[:3]


def tool_find_perks(stack: str) -> list[dict]:
    return data.perks()


def _run_tool(name: str, args: dict, profile: dict, ctx: dict):
    # In edit sessions ctx["state"] is the live, mutating profile.
    eff = ctx.get("state", profile)
    if name == "build_plan":
        return tool_build_plan(eff)
    if name == "now_next":
        return tool_now_next(eff, args.get("date", ctx.get("date")), args.get("time", ctx.get("time")))
    if name == "search_sessions":
        return tool_search_sessions(args.get("query", ""))
    if name == "find_mentors":
        return tool_find_mentors(args.get("topic", ""))
    if name == "find_perks":
        return tool_find_perks(args.get("stack", ""))
    if name in ("replace_session", "remove_session"):
        sid = args.get("session_id", "")
        action = "replace" if name == "replace_session" else "remove"
        plan, new_state, info = planner.edit_plan(eff, action, sid)
        ctx["state"] = new_state
        ctx["plan"] = plan
        by_id = {s["id"]: s for s in data.sessions()}
        added = [by_id[i]["title"] for i in info["added"] if i in by_id]
        return {"removed_id": sid, "slot_filled": info["filled"],
                "now_filled_by": added,
                "note": ("" if info["filled"]
                         else "No other session occupies that time window, so the slot is now free.")}
    return {"error": f"unknown tool {name}"}


def _summarize(name: str, result) -> str:
    if name == "build_plan":
        n = sum(len(d["sessions"]) for d in result.get("days", []))
        return f"{n} sessions across {len(result.get('days', []))} days"
    if name == "now_next":
        bits = [k for k in ("now", "next", "dont_miss") if result.get(k)]
        return "computed " + ", ".join(bits)
    if isinstance(result, list):
        return f"{len(result)} results"
    return "ok"


TOOLSPEC = [
    {"toolSpec": {"name": "search_sessions", "description": "Find sessions matching a query (topic, partner, tag).",
                  "inputSchema": {"json": {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]}}}},
    {"toolSpec": {"name": "find_mentors", "description": "Find mentors/judges for a topic or track.",
                  "inputSchema": {"json": {"type": "object", "properties": {"topic": {"type": "string"}}, "required": ["topic"]}}}},
    {"toolSpec": {"name": "find_perks", "description": "Find credits/perks the builder can claim.",
                  "inputSchema": {"json": {"type": "object", "properties": {"stack": {"type": "string"}}, "required": ["stack"]}}}},
    {"toolSpec": {"name": "now_next", "description": "What to do now/next/not-miss for a date and time.",
                  "inputSchema": {"json": {"type": "object", "properties": {"date": {"type": "string"}, "time": {"type": "string"}}, "required": []}}}},
    {"toolSpec": {"name": "build_plan", "description": "Compute the builder's conflict-free, travel-aware personal schedule.",
                  "inputSchema": {"json": {"type": "object", "properties": {}}}}},
]

EDIT_TOOLSPEC = TOOLSPEC + [
    {"toolSpec": {"name": "replace_session", "description": "Replace ONE session (by id) with a better alternative for the builder's goals. Every other session stays exactly as is.",
                  "inputSchema": {"json": {"type": "object", "properties": {"session_id": {"type": "string"}}, "required": ["session_id"]}}}},
    {"toolSpec": {"name": "remove_session", "description": "Remove ONE session (by id) from the plan. Every other session stays exactly as is.",
                  "inputSchema": {"json": {"type": "object", "properties": {"session_id": {"type": "string"}}, "required": ["session_id"]}}}},
]

ASK_SYSTEM = (
    "You are Compass, the on-site copilot for Agentic AI Build Week, Ho Chi Minh City. "
    "Use the tools to ground every answer in the real schedule. Be concrete and short: "
    "name sessions, times, venues, and the single next action. Never invent anything the tools did not return."
)


def _bedrock_loop(seed_text: str, system: str, profile: dict, ctx: dict, max_turns: int = 5, tools=None):
    """Run the Converse tool-use loop. Returns dict or None if Bedrock is unusable."""
    try:
        import boto3
    except Exception:
        return None
    tools = tools or TOOLSPEC
    model_id = os.environ.get("BEDROCK_MODEL_ID", "us.anthropic.claude-sonnet-4-5-20250929-v1:0")
    region = os.environ.get("AWS_REGION", "us-east-1")
    try:
        client = boto3.client("bedrock-runtime", region_name=region)
        messages = [{"role": "user", "content": [{"text": seed_text}]}]
        trace, captured = [], {}
        for _ in range(max_turns):
            resp = client.converse(
                modelId=model_id, system=[{"text": system}], messages=messages,
                toolConfig={"tools": tools},
                inferenceConfig={"maxTokens": 700, "temperature": 0.2},
            )
            out = resp["output"]["message"]
            messages.append(out)
            text = "".join(b.get("text", "") for b in out["content"] if "text" in b).strip()
            if resp.get("stopReason") == "tool_use":
                if text:
                    trace.append({"type": "think", "text": text})
                results = []
                for block in out["content"]:
                    if "toolUse" not in block:
                        continue
                    tu = block["toolUse"]
                    res = _run_tool(tu["name"], tu.get("input", {}), profile, ctx)
                    captured[tu["name"]] = res
                    trace.append({"type": "tool", "tool": tu["name"],
                                  "input": tu.get("input", {}), "summary": _summarize(tu["name"], res)})
                    results.append({"toolResult": {"toolUseId": tu["toolUseId"],
                                    "content": [{"json": res if isinstance(res, dict) else {"data": res}}]}})
                messages.append({"role": "user", "content": results})
                continue
            if text:
                trace.append({"type": "answer", "text": text})
            return {"final": text, "trace": trace, "captured": captured}
        return {"final": text, "trace": trace, "captured": captured}
    except Exception as e:
        return {"final": None, "trace": [{"type": "error", "text": str(e)[:160]}], "captured": {}, "_error": True}


# ---- Public entrypoints -----------------------------------------------------


def run_ask(question: str, profile: dict, ctx: dict) -> dict:
    res = _bedrock_loop(question, ASK_SYSTEM, profile, ctx)
    if res and not res.get("_error") and res.get("final"):
        return {"answer": res["final"], "trace": res["trace"], "brain": "bedrock"}
    return _fallback_ask(question, profile, ctx)


def run_plan(profile: dict, ctx: dict) -> dict:
    """Agent-driven planning: the model investigates, then calls build_plan."""
    goals = ", ".join(profile.get("goals") or []) or "general"
    track = profile.get("track") or "no specific track"
    cv = ""
    if profile.get("cv_summary"):
        skills = ", ".join(profile.get("skills") or [])
        cv = (f"Their background, from their CV: {profile['cv_summary']}. "
              f"Skills: {skills}. Weigh sessions, mentors and perks toward this background, "
              "and justify picks against both their goals and their experience. ")
    seed = (
        f"Plan {profile.get('name') or 'this builder'}'s week at Agentic AI Build Week. "
        f"Their goals: {goals}. Track: {track}. {cv}"
        "First investigate what matters using search_sessions, and find_perks and/or "
        "find_mentors if relevant. Then call build_plan to produce the schedule. "
        "Finally write a 2-sentence strategy summary of how this week serves their goals"
        + (" and their background." if cv else ".")
    )
    res = _bedrock_loop(seed, ASK_SYSTEM, profile, ctx, max_turns=8)
    if res and not res.get("_error"):
        plan = res["captured"].get("build_plan") or planner.build_plan(profile)
        return {"summary": res.get("final") or "Plan ready.", "plan": plan,
                "trace": res["trace"], "brain": "bedrock"}
    return _fallback_plan(profile, ctx)


EDIT_SYSTEM = (
    "You are Compass, editing a builder's Agentic AI Build Week plan. The user is discussing "
    "ONE specific session. If they want it changed, call replace_session or remove_session with "
    "that session's id; this keeps every other session fixed. If they only want an explanation, "
    "answer directly using the tools. Be concise and concrete. "
    "IMPORTANT: report exactly what the edit tool returned. If slot_filled is true, name the "
    "session in now_filled_by that now occupies that time. If slot_filled is false, tell the user "
    "there was no other session in that time slot, so it is now free. Never claim a replacement "
    "that did not happen, and never invent a session."
)


def run_edit(profile: dict, instruction: str, focus: dict, ctx: dict) -> dict:
    """Conversational edit scoped to one session. focus = {id,title,start,venue}."""
    ctx = {**ctx, "state": dict(profile), "plan": None}
    fdesc = f"id={focus.get('id')}, \"{focus.get('title')}\" at {focus.get('start')} ({focus.get('venue')})" if focus else "(none)"
    seed = (
        f"The builder is discussing this session: {fdesc}.\n"
        f"Their request: \"{instruction}\"\n"
        "If they want it swapped or removed, call the matching tool with its id. "
        "Then reply in one or two sentences explaining what you changed (or answer their question)."
    )
    res = _bedrock_loop(seed, EDIT_SYSTEM, profile, ctx, max_turns=6, tools=EDIT_TOOLSPEC)
    if res and not res.get("_error") and res.get("final"):
        plan = ctx.get("plan")
        out = {"answer": res["final"], "trace": res["trace"], "brain": "bedrock",
               "state": ctx["state"]}
        if plan is not None:
            out["plan"] = plan
        return out
    return _fallback_edit(profile, instruction, focus, ctx)


def _fallback_edit(profile: dict, instruction: str, focus: dict, ctx: dict) -> dict:
    q = (instruction or "").lower()
    sid = (focus or {}).get("id")
    trace = [{"type": "think", "text": f"edit request on {sid}"}]
    if sid and any(w in q for w in ["replace", "swap", "change", "different", "instead", "another", "remove", "delete", "drop"]):
        action = "remove" if any(w in q for w in ["remove", "delete", "drop"]) else "replace"
        plan, new_state, info = planner.edit_plan(profile, action, sid)
        by_id = {s["id"]: s for s in data.sessions()}
        added = [by_id[i]["title"] for i in info["added"] if i in by_id]
        trace.append({"type": "tool", "tool": f"{action}_session", "input": {"session_id": sid},
                      "summary": ("filled by " + ", ".join(added)) if info["filled"] else "removed, slot now free"})
        if info["filled"]:
            ans = f"Done. That slot is now {', '.join(added)}, and the rest of your plan is unchanged."
        else:
            ans = "Done. There's no other session in that time slot, so I removed it and that time is now free. Everything else stays the same."
        trace.append({"type": "answer", "text": ans})
        return {"answer": ans, "trace": trace, "brain": "local", "state": new_state, "plan": plan}
    # explain
    ans = f"That session is \"{(focus or {}).get('title','')}\" at {(focus or {}).get('start','')}. Ask me to replace or remove it, or what to do around it."
    trace.append({"type": "answer", "text": ans})
    return {"answer": ans, "trace": trace, "brain": "local", "state": profile}


# ---- Deterministic fallback (same tools, transparent 'local' label) ---------


INTEREST_VOCAB = ["ai", "agents", "cloud", "aws", "ml", "backend", "gaming", "healthcare",
                  "retail", "fintech", "robotics", "mobile", "design", "startup", "credits", "media"]


def _bedrock_text(system: str, prompt: str) -> str | None:
    try:
        import boto3
    except Exception:
        return None
    model_id = os.environ.get("BEDROCK_MODEL_ID", "us.anthropic.claude-sonnet-4-5-20250929-v1:0")
    region = os.environ.get("AWS_REGION", "us-east-1")
    try:
        client = boto3.client("bedrock-runtime", region_name=region)
        resp = client.converse(
            modelId=model_id, system=[{"text": system}],
            messages=[{"role": "user", "content": [{"text": prompt}]}],
            inferenceConfig={"maxTokens": 500, "temperature": 0.1},
        )
        return "".join(b.get("text", "") for b in resp["output"]["message"]["content"]).strip()
    except Exception:
        return None


def extract_cv_profile(cv_text: str) -> dict:
    """Read a CV and return a profile patch to personalize the plan."""
    import json
    goal_ids = list(planner.GOAL_LABELS.keys())
    track_ids = [t["id"] for t in data.tracks()]
    cv_text = (cv_text or "").strip()
    if not cv_text:
        return {"summary": "", "skills": [], "interests": [], "suggested_goals": [],
                "suggested_track": None, "brain": "none"}

    system = ("You read a builder's CV/resume and extract a compact JSON profile to personalize "
              "their week at Agentic AI Build Week. Return ONLY a JSON object, no prose.")
    prompt = (
        "Extract this JSON (use the exact keys):\n"
        "{\"summary\": one short sentence about them,\n"
        f" \"skills\": up to 8 short skill keywords,\n"
        f" \"interests\": up to 8 tags chosen ONLY from {INTEREST_VOCAB},\n"
        f" \"suggested_goals\": subset of {goal_ids},\n"
        f" \"suggested_track\": one of {track_ids} or null }}\n\n"
        f"CV:\n{cv_text[:6000]}"
    )
    raw = _bedrock_text(system, prompt)
    if raw:
        try:
            txt = raw.strip()
            if "```" in txt:
                txt = txt.split("```")[1].replace("json", "", 1).strip() if txt.count("```") >= 2 else txt
            txt = txt[txt.find("{"): txt.rfind("}") + 1]
            obj = json.loads(txt)
            obj["interests"] = [i for i in obj.get("interests", []) if i in INTEREST_VOCAB][:8]
            obj["suggested_goals"] = [g for g in obj.get("suggested_goals", []) if g in goal_ids]
            if obj.get("suggested_track") not in track_ids:
                obj["suggested_track"] = None
            obj["brain"] = "bedrock"
            return obj
        except Exception:
            pass
    return _fallback_cv(cv_text, goal_ids, track_ids)


def _fallback_cv(cv_text: str, goal_ids: list, track_ids: list) -> dict:
    t = cv_text.lower()
    interests = [w for w in INTEREST_VOCAB if w in t][:8]
    goals = []
    if any(w in t for w in ["founder", "startup", "ceo", "co-founder"]):
        goals += ["meet_investors", "credits"]
    if any(w in t for w in ["junior", "student", "graduate", "learning"]):
        goals.append("learn_stack")
    goals.append("find_team")
    track = None
    for tid in track_ids:
        if tid in t:
            track = tid
            break
    return {"summary": "Profile inferred from your CV keywords.",
            "skills": interests[:6], "interests": interests,
            "suggested_goals": list(dict.fromkeys(goals))[:3],
            "suggested_track": track, "brain": "local"}


def _fallback_ask(question: str, profile: dict, ctx: dict) -> dict:
    q = question.lower()
    trace = [{"type": "think", "text": f"routing intent for: {question[:60]}"}]
    if any(w in q for w in ["now", "next", "where do i go", "what should i do"]):
        nn = tool_now_next(profile, ctx.get("date"), ctx.get("time"))
        trace.append({"type": "tool", "tool": "now_next", "input": ctx, "summary": _summarize("now_next", nn)})
        parts = []
        if nn.get("now"): parts.append(f"Now: {nn['now']['title']} at {nn['now']['venue']}.")
        if nn.get("next"):
            n = nn["next"]; parts.append(f"Next: {n['title']} at {n['start']} ({n['venue']}), leave in ~{n['leave_in_min']} min.")
        if nn.get("dont_miss"): parts.append(f"Don't miss: {nn['dont_miss']['title']} ({nn['dont_miss']['start']}).")
        ans = " ".join(parts) or "Nothing scheduled at that moment."
    elif any(w in q for w in ["credit", "perk", "free", "claim"]):
        ps = tool_find_perks(q); trace.append({"type": "tool", "tool": "find_perks", "input": {"stack": q}, "summary": _summarize("find_perks", ps)})
        ans = " | ".join(f"{p['name']}: {p['value']} ({p['claim']})" for p in ps)
    elif any(w in q for w in ["mentor", "judge", "meet", "who"]):
        ms = tool_find_mentors(q); trace.append({"type": "tool", "tool": "find_mentors", "input": {"topic": q}, "summary": _summarize("find_mentors", ms)})
        ans = " | ".join(f"{m['name']}, {m['role']} ({m['org']})" for m in ms)
    else:
        hits = tool_search_sessions(q); trace.append({"type": "tool", "tool": "search_sessions", "input": {"query": q}, "summary": _summarize("search_sessions", hits)})
        ans = " | ".join(f"{h['title']} (Day {h['day']}, {h['start']}, {h['venue']})" for h in hits) or \
              "Ask what to do now, where to claim credits, or who to meet."
    trace.append({"type": "answer", "text": ans})
    return {"answer": ans, "trace": trace, "brain": "local"}


def _fallback_plan(profile: dict, ctx: dict) -> dict:
    goals = profile.get("goals") or []
    trace = [{"type": "think", "text": "goal analysis: " + (", ".join(goals) or "general")}]
    q = "credits" if "credits" in goals else ("networking" if "find_team" in goals else "ai")
    hits = tool_search_sessions(q)
    trace.append({"type": "tool", "tool": "search_sessions", "input": {"query": q}, "summary": _summarize("search_sessions", hits)})
    if "credits" in goals:
        ps = tool_find_perks(q); trace.append({"type": "tool", "tool": "find_perks", "input": {"stack": q}, "summary": _summarize("find_perks", ps)})
    if profile.get("track"):
        ms = tool_find_mentors(profile["track"]); trace.append({"type": "tool", "tool": "find_mentors", "input": {"topic": profile["track"]}, "summary": _summarize("find_mentors", ms)})
    plan = tool_build_plan(profile)
    trace.append({"type": "tool", "tool": "build_plan", "input": {}, "summary": _summarize("build_plan", plan)})
    n = sum(len(d["sessions"]) for d in plan["days"])
    summary = f"Built a {n}-session plan prioritising {', '.join(goals) or 'a balanced week'}, conflict-free and routed across venues."
    trace.append({"type": "answer", "text": summary})
    return {"summary": summary, "plan": plan, "trace": trace, "brain": "local"}
