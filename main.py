"""
AI Boss — backend core
Receives inbound messages from WhatsApp, Email, and Call-transcript webhooks,
routes each one to the right agent using the industry module config below,
and exposes a /tasks API for the admin dashboard to read/update.

Tasks are stored in a real database now:
- If Railway's DATABASE_URL is set (add the Postgres plugin in Railway),
  tasks persist permanently, surviving restarts and redeploys.
- If not set, falls back to a local SQLite file — fine for testing, but
  Railway's filesystem isn't guaranteed to persist that file across
  redeploys, so add Postgres before relying on this for real.

Run locally:   uvicorn main:app --reload
Deploy:        push this folder to GitHub, connect the repo on Railway.
"""

import os
import pathlib
from datetime import datetime, timezone
from typing import Optional

from fastapi import FastAPI, Request, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy import create_engine, Column, Integer, String, Boolean, DateTime, func
from sqlalchemy.orm import declarative_base, sessionmaker

app = FastAPI(title="AI Boss")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---------------------------------------------------------------------------
# DATABASE
# ---------------------------------------------------------------------------

DATABASE_URL = os.environ.get("DATABASE_URL", "sqlite:///./ai_boss.db")
# Railway (and most providers) hand out "postgres://" — SQLAlchemy 2.x wants
# "postgresql://". This line quietly fixes that so you don't hit a startup
# crash the first time you add the Postgres plugin.
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

connect_args = {"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {}
engine = create_engine(DATABASE_URL, connect_args=connect_args)
SessionLocal = sessionmaker(bind=engine)
Base = declarative_base()


class TaskRow(Base):
    __tablename__ = "tasks"
    id = Column(Integer, primary_key=True, autoincrement=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    source = Column(String)
    module = Column(String)
    agent = Column(String)
    status = Column(String, default="Routed")
    preview = Column(String)
    customer_key = Column(String, nullable=True)
    is_returning = Column(Boolean, default=False)
    notes = Column(String, default="")


Base.metadata.create_all(engine)


def row_to_dict(row: TaskRow):
    return {
        "id": row.id,
        "created_at": (row.created_at or datetime.now(timezone.utc)).isoformat(),
        "source": row.source,
        "module": row.module,
        "agent": row.agent,
        "status": row.status,
        "preview": row.preview,
        "customer_key": row.customer_key,
        "is_returning": row.is_returning,
        "notes": row.notes or "",
    }


# ---------------------------------------------------------------------------
# INDUSTRY MODULE CONFIG
# One object per industry. Adding a new industry later = adding a new key
# here, not rewriting the routing logic below.
# ---------------------------------------------------------------------------

MODULES = {
    "travel": {
        "label": "Travel — New 18",
        "agents": {
            "calling": {"name": "Calling Agent", "keywords": ["call", "phone", "confirm", "pickup"]},
            "email": {"name": "Email Agent", "keywords": ["quote", "pricing", "inquiry", "lead", "interested"]},
            "reminder": {"name": "Reminder Agent", "keywords": ["due", "payment", "reminder", "renew", "expire"]},
            "arranging": {"name": "Arranging / Itinerary Agent", "keywords": ["itinerary", "finalize", "arrange", "book", "plan"]},
        },
        "default_agent": "email",
    }
}


def pick_agent(text: str, module: str = "travel"):
    lower = text.lower()
    agents = MODULES[module]["agents"]
    best_key, best_score = None, 0
    for key, agent in agents.items():
        score = sum(1 for kw in agent["keywords"] if kw in lower)
        if score > best_score:
            best_key, best_score = key, score
    if not best_key:
        best_key = MODULES[module]["default_agent"]
    return best_key, agents[best_key]["name"], best_score


# ---------------------------------------------------------------------------
# TASK STORE — now backed by the database above
# ---------------------------------------------------------------------------

class TaskStore:
    def add(self, source: str, agent: str, module: str, preview: str,
            customer_key: Optional[str] = None, status: str = "Routed"):
        db = SessionLocal()
        try:
            is_returning = False
            if customer_key:
                is_returning = db.query(TaskRow).filter(
                    func.lower(TaskRow.customer_key) == customer_key.lower()
                ).first() is not None
            row = TaskRow(
                source=source, module=module, agent=agent, status=status,
                preview=preview, customer_key=customer_key, is_returning=is_returning, notes="",
            )
            db.add(row)
            db.commit()
            db.refresh(row)
            return row_to_dict(row)
        finally:
            db.close()

    def list(self, status: Optional[str] = None):
        db = SessionLocal()
        try:
            q = db.query(TaskRow)
            if status:
                q = q.filter(TaskRow.status == status)
            rows = q.order_by(TaskRow.id.desc()).all()
            return [row_to_dict(r) for r in rows]
        finally:
            db.close()

    def update(self, task_id: int, status: Optional[str] = None, notes: Optional[str] = None):
        db = SessionLocal()
        try:
            row = db.query(TaskRow).filter(TaskRow.id == task_id).first()
            if not row:
                raise HTTPException(404, "Task not found")
            if status:
                row.status = status
            if notes is not None:
                row.notes = notes
            db.commit()
            db.refresh(row)
            return row_to_dict(row)
        finally:
            db.close()

    def clear(self):
        db = SessionLocal()
        try:
            db.query(TaskRow).delete()
            db.commit()
        finally:
            db.close()


store = TaskStore()


# ---------------------------------------------------------------------------
# ADMIN / DASHBOARD API
# ---------------------------------------------------------------------------

class TaskUpdate(BaseModel):
    status: Optional[str] = None
    notes: Optional[str] = None


class TaskCreate(BaseModel):
    text: str
    customer_key: Optional[str] = None
    source: Optional[str] = "Manual"


@app.get("/")
def health():
    return {"status": "AI Boss backend is running"}


@app.get("/dashboard")
def dashboard():
    return FileResponse(pathlib.Path(__file__).parent / "dashboard.html")


@app.get("/tasks")
def list_tasks(status: Optional[str] = Query(None)):
    return store.list(status)


@app.post("/tasks")
def create_task(body: TaskCreate):
    """Manual entry point — the dashboard's '+ New Task' box uses this.
    Runs the same routing logic as the webhooks, so a walk-in or phone
    inquiry the admin types in gets routed exactly like a real one."""
    _, agent_name, _ = pick_agent(body.text)
    return store.add(source=body.source, agent=agent_name, module="travel",
                      preview=body.text[:80], customer_key=body.customer_key)


@app.patch("/tasks/{task_id}")
def update_task(task_id: int, body: TaskUpdate):
    return store.update(task_id, status=body.status, notes=body.notes)


@app.post("/tasks/seed")
def seed_test_data():
    """Loads a handful of realistic sample tasks — lets you see the
    dashboard working with real data without waiting on WhatsApp/Twilio."""
    samples = [
        ("WhatsApp", "New inquiry: 4 people, Ladakh, first week of October. Wants a quote.", "919876500001"),
        ("Email", "Payment for booking #New18-2214 is due in 3 days.", "guest2214@example.com"),
        ("Call transcript", "Caller asked to confirm pickup time for tomorrow's Srinagar tour.", "919876500002"),
        ("WhatsApp", "Finalize itinerary for the Sharma family, 5 days Kashmir.", "919876500001"),
    ]
    created = []
    for source, text, key in samples:
        _, agent_name, _ = pick_agent(text)
        created.append(store.add(source=source, agent=agent_name, module="travel",
                                  preview=text[:80], customer_key=key))
    return {"created": len(created)}


@app.delete("/tasks")
def clear_tasks():
    """Wipes all tasks — for testing only. No confirmation here on the
    API itself; the dashboard button asks before calling this."""
    store.clear()
    return {"ok": True}


# ---------------------------------------------------------------------------
# WHATSAPP WEBHOOK (Meta Cloud API)
# ---------------------------------------------------------------------------

WHATSAPP_VERIFY_TOKEN = os.environ.get("WHATSAPP_VERIFY_TOKEN", "changeme")


@app.get("/webhooks/whatsapp")
def whatsapp_verify(
    hub_mode: str = Query(None, alias="hub.mode"),
    hub_challenge: str = Query(None, alias="hub.challenge"),
    hub_verify_token: str = Query(None, alias="hub.verify_token"),
):
    if hub_mode == "subscribe" and hub_verify_token == WHATSAPP_VERIFY_TOKEN:
        return int(hub_challenge)
    raise HTTPException(403, "Verification failed")


@app.post("/webhooks/whatsapp")
async def whatsapp_incoming(request: Request):
    payload = await request.json()
    try:
        entry = payload["entry"][0]["changes"][0]["value"]
        message = entry["messages"][0]
        phone = message["from"]
        text = message.get("text", {}).get("body", "")
    except (KeyError, IndexError):
        return {"ok": True}

    _, agent_name, _ = pick_agent(text)
    store.add(source="WhatsApp", agent=agent_name, module="travel",
              preview=text[:80], customer_key=phone)
    return {"ok": True}


# ---------------------------------------------------------------------------
# EMAIL WEBHOOK (SendGrid Inbound Parse / Mailgun routes)
# ---------------------------------------------------------------------------

@app.post("/webhooks/email")
async def email_incoming(request: Request):
    form = await request.form()
    sender = form.get("from", "unknown@sender")
    subject = form.get("subject", "")
    body = form.get("text", "") or form.get("body-plain", "")
    text = f"{subject} — {body}".strip(" —")

    _, agent_name, _ = pick_agent(text)
    store.add(source="Email", agent=agent_name, module="travel",
              preview=text[:80], customer_key=sender)
    return {"ok": True}


# ---------------------------------------------------------------------------
# CALL TRANSCRIPT WEBHOOK (Twilio)
# ---------------------------------------------------------------------------

@app.post("/webhooks/call")
async def call_incoming(request: Request):
    form = await request.form()
    caller = form.get("From", "unknown")
    transcript = form.get("TranscriptionText", "")

    _, agent_name, _ = pick_agent(transcript)
    store.add(source="Call transcript", agent=agent_name, module="travel",
              preview=transcript[:80], customer_key=caller)
    return {"ok": True}
