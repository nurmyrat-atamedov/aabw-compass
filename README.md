# 🧭 Compass — the AABW on-site copilot

**Never miss the thing you came for.**

**🔴 Live demo: https://54-81-185-226.sslip.io**  ·  Built for the Builder Experience Track.

Agentic AI Build Week is 5 days, 2,000 builders, 3+ venues, 30+ workshops,
limited seats, and a flood of perks and deadlines. Every builder asks the same
question dozens of times a day: *"where am I supposed to be right now, and am I
missing something better?"*

Compass answers that question — personally, in real time, all week.

Built for the **Builder Experience Track** at Agentic AI Build Week.

## What it does

You spend 15 seconds telling Compass what you're here for (collect credits,
find a team, learn the stack, meet investors, win a track). Then:

- **NOW / NEXT / DON'T MISS** — a glanceable card that always tells you the one
  thing to do right now, where to go next, and **when to leave** (it accounts
  for travel time between venues). The proactive *"leave in 18 min"* nudge is
  the part that saves you from missing the talk you came for.
- **My plan** — a personalized, **conflict-free, travel-aware** agenda for all
  5 days, with a one-line *why* on every pick. Export to your calendar (.ics)
  so your own phone fires the reminders. No notification infra needed.
- **Ask Compass** — a real agent (not a chatbot). Ask *"what should I do in the
  next hour?"*, *"where do I claim credits?"*, *"who should I meet for the
  Guardian track?"* and it calls tools against the live schedule to answer with
  concrete sessions, times, venues, and a next action.
- **Upload your CV** — the agent reads your resume (PDF/text via Bedrock),
  extracts your skills, interests, and seniority, auto-selects your goals and a
  suggested track, and biases the whole plan toward sessions, mentors, and perks
  that fit your background.

## Why it's an agent, not a chatbot

The "Ask" feature runs an **Amazon Bedrock** (Converse API + tool use) loop.
The model plans over five tools — `build_plan`, `now_next`, `search_sessions`,
`find_mentors`, `find_perks` — and grounds every answer in the event data. It
never free-associates. If no AWS credentials are present, it falls back to a
deterministic intent router that calls the **same tools**, so the product is
fully usable with zero keys.

The planner itself is deterministic constraint-solving (weighted interval
scheduling with a venue-travel feasibility constraint) — so your plan is
*solved*, not guessed, and the demo never depends on a model being up.

## Data = the integration contract

Everything Compass reasons over lives in **`data/aabw.json`** (real, public AABW
schedule, venues, tracks, mentors, perks, key dates). The schema is the
integration point: **organizers just drop authoritative data (live seat counts,
room changes, last-minute sessions) into that one file** to deploy it for
thousands. No internal systems, no code changes.

## Run it

```bash
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8000
# open http://localhost:8000
```

Optional — turn on the Bedrock brain (otherwise the local fallback is used):

```bash
export AWS_REGION=us-east-1
export BEDROCK_MODEL_ID=us.anthropic.claude-3-5-haiku-20241022-v1:0
# plus standard AWS credentials in your environment
```

Tip for the demo: the event is in July, so use the **"Simulate moment"**
control (top of the app) to jump to any day/time during Build Week and watch
NOW / NEXT / DON'T MISS update live.

## Deploy on AWS EC2 (with the real Bedrock agent)

1. **Launch** an Amazon Linux 2023 instance. Security group: open inbound
   **8000** (or 80 if you front it with nginx) and 22.
2. **Give it Bedrock access.** Create an IAM role with the policy in
   `deploy/bedrock-policy.json`, attach it to the instance. Make sure Claude
   3.5 Haiku model access is enabled in your region (Bedrock console → Model
   access). boto3 picks up the instance role automatically, no keys on disk.
3. **Ship the code** and run setup:
   ```bash
   scp -i key.pem -r aabw-compass ec2-user@<IP>:~/        # or git clone
   ssh -i key.pem ec2-user@<IP>
   cd ~/aabw-compass && bash deploy/setup_ec2.sh
   ```
   That creates a venv, installs deps, installs the `compass` systemd service,
   and starts it. Your live link is `http://<IP>:8000`.
4. **Verify the agent is on Bedrock:** open the app, click "Plan my week", and
   the Agent badge should read **● Bedrock agent** (not `○ local fallback`).
   The trace shows the model's real tool calls.

Update later with `git pull && sudo systemctl restart compass`.
Logs: `journalctl -u compass -f`.

## Stack

FastAPI · Amazon Bedrock (Converse + tool use) · vanilla JS + Tailwind ·
deterministic scheduler in pure Python. Self-contained, public data, MIT.
