"""Compass - the AABW on-site copilot. FastAPI app + static UI."""
from pathlib import Path

from fastapi import FastAPI, File, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, Response
from pydantic import BaseModel

from . import agent, cv, data, ics, planner

app = FastAPI(title="AABW Compass", version="0.1.0")
WEB = Path(__file__).resolve().parent.parent / "web"


class Profile(BaseModel):
    name: str | None = None
    goals: list[str] = []
    track: str | None = None
    interests: list[str] = []
    skills: list[str] = []
    cv_summary: str | None = None
    pinned: list[str] = []
    excluded: list[str] = []


class FocusSession(BaseModel):
    id: str | None = None
    title: str | None = None
    start: str | None = None
    venue: str | None = None


class EditReq(BaseModel):
    profile: Profile
    instruction: str
    focus: FocusSession | None = None
    date: str | None = None
    time: str | None = None


class NowReq(BaseModel):
    profile: Profile
    date: str
    time: str


class AskReq(BaseModel):
    profile: Profile
    question: str
    date: str
    time: str


@app.get("/", response_class=HTMLResponse)
def index():
    return FileResponse(WEB / "index.html")


@app.get("/api/meta")
def meta():
    return {
        "event": data.load()["event"],
        "tracks": [{"id": t["id"], "name": t["name"], "sponsor": t["sponsor"]} for t in data.tracks()],
        "goals": list(planner.GOAL_LABELS.items()),
        "key_dates": data.key_dates(),
        "perks": data.perks(),
        "mentors": data.mentors(),
        "venues": [v for v in data.load()["venues"] if v["id"] != "tbd"],
        "stats": {
            "sessions": len(data.sessions()),
            "tracks": len(data.tracks()),
            "venues": len([v for v in data.load()["venues"] if v["id"] != "tbd"]),
            "mentors": len(data.mentors()),
        },
    }


@app.post("/api/plan")
def plan(p: Profile):
    # Deterministic plan (the scheduler tool, used as a fast fallback).
    return planner.build_plan(p.model_dump())


@app.post("/api/agent/plan")
def agent_plan(r: NowReq):
    # Agent-driven: the model investigates with tools, then calls build_plan.
    return agent.run_plan(r.profile.model_dump(), {"date": r.date, "time": r.time})


@app.post("/api/now")
def now(r: NowReq):
    return planner.now_next(r.profile.model_dump(), r.date, r.time)


@app.post("/api/ask")
def ask(r: AskReq):
    return agent.run_ask(r.question, r.profile.model_dump(), {"date": r.date, "time": r.time})


@app.post("/api/agent/edit")
def agent_edit(r: EditReq):
    focus = r.focus.model_dump() if r.focus else {}
    return agent.run_edit(r.profile.model_dump(), r.instruction, focus,
                          {"date": r.date, "time": r.time})


@app.post("/api/cv")
async def upload_cv(file: UploadFile = File(...)):
    raw = await file.read()
    text = cv.extract_text(file.filename, raw)
    return agent.extract_cv_profile(text)


@app.post("/api/ics")
def calendar(p: Profile):
    body = ics.plan_to_ics(p.model_dump())
    return Response(
        content=body,
        media_type="text/calendar",
        headers={"Content-Disposition": "attachment; filename=aabw-plan.ics"},
    )
