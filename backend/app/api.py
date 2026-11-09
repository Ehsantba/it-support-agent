"""REST-API (FastAPI).

Ticket-Eingang und Lesen sind offen (wie ein Portal). Freigabe, Rückgängig, Abschlussprüfung und Audit-Log brauchen
den Reviewer-Token (Header X-Reviewer-Token) und einen Namen (Header X-Reviewer).
"""
from __future__ import annotations

import hmac
import re
from pathlib import Path
from typing import Literal

from fastapi import BackgroundTasks, Depends, FastAPI, Header, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import audit
from .agent import Agent
from .config import Settings
from .db import Database
from .directory.base import Directory, DirectoryError
from .directory.simulated import SimulatedDirectory, reset_simulation
from .store import Store
from .workflow import Workflow, WorkflowError


class TicketIn(BaseModel):
    requester_upn: str = Field(min_length=5, max_length=120)
    subject: str = Field(min_length=3, max_length=200)
    body: str = Field(min_length=5, max_length=4000)
    dry_run: bool = False


class DecisionIn(BaseModel):
    approve: bool
    comment: str | None = Field(default=None, max_length=500)


class ReviewIn(BaseModel):
    verdict: Literal["approve", "reject"]
    comment: str | None = Field(default=None, max_length=500)


def create_app(settings: Settings, db: Database, directory: Directory, agent: Agent,
               frontend_dir: Path | None = None) -> FastAPI:
    app = FastAPI(title="IT-Support-Agent", version="1.0.0")
    store = Store(db)
    workflow = Workflow(store, directory, agent.tools)
    domains = settings.allowed_domains

    def reviewer(x_reviewer_token: str | None = Header(default=None), x_reviewer: str | None = Header(default=None)) -> str:
        if not x_reviewer_token or not hmac.compare_digest(x_reviewer_token.encode(), settings.reviewer_token.encode()):
            raise HTTPException(401, "Reviewer-Token fehlt oder ist falsch.")
        name = (x_reviewer or "").strip()
        if not re.fullmatch(r"[\w.@\- ]{2,80}", name):
            raise HTTPException(422, "Bitte einen Reviewer-Namen angeben (Header X-Reviewer).")
        return name

    def wf(call):
        try:
            return call()
        except WorkflowError as exc:
            raise HTTPException(exc.status, str(exc)) from exc

    @app.get("/api/health")
    def health() -> dict:
        with db.connection() as conn:
            conn.execute("SELECT 1")
        return {"status": "ok"}

    @app.get("/api/config")
    def config() -> dict:
        return {"llm": agent.llm.name, "directory": directory.name, "dry_run": settings.dry_run,
                "allowed_domains": list(domains), "max_steps": settings.max_steps}

    @app.post("/api/tickets", status_code=201)
    def create_ticket(t: TicketIn, background: BackgroundTasks) -> dict:
        upn = t.requester_upn.strip().lower()
        if not any(upn.endswith("@" + d) for d in domains) or not re.fullmatch(r"[a-z0-9._%+\-]{1,64}@[a-z0-9.\-]+", upn):
            raise HTTPException(422, f"Der Antragsteller muss ein Konto der Domäne {', '.join(domains)} sein.")
        try:
            directory.get_account_status(upn)
        except DirectoryError:
            raise HTTPException(422, "Der Antragsteller ist unbekannt.")
        tid = store.create_ticket(upn, t.subject.strip(), t.body.strip(), t.dry_run)
        if settings.auto_process:
            background.add_task(agent.process, tid)
        return {"id": tid, "status": "NEW"}

    @app.get("/api/tickets")
    def tickets(status: str | None = None) -> list[dict]:
        return store.list_tickets(status)

    @app.get("/api/tickets/{tid}")
    def ticket(tid: int) -> dict:
        t = store.get_ticket(tid)
        if t is None:
            raise HTTPException(404, "Ticket nicht gefunden.")
        return {"ticket": t, "actions": store.actions_of(tid)}

    @app.post("/api/tickets/{tid}/process")
    def process(tid: int, background: BackgroundTasks, who: str = Depends(reviewer)) -> dict:
        t = store.get_ticket(tid)
        if t is None:
            raise HTTPException(404, "Ticket nicht gefunden.")
        if t["status"] not in ("NEW", "FAILED"):
            raise HTTPException(409, "Das Ticket wurde schon bearbeitet.")
        background.add_task(agent.process, tid)
        return {"id": tid, "queued": True}

    @app.post("/api/tickets/{tid}/review")
    def review(tid: int, body: ReviewIn, who: str = Depends(reviewer)) -> dict:
        return wf(lambda: workflow.review(tid, body.verdict, body.comment, who))

    @app.get("/api/approvals")
    def approvals(who: str = Depends(reviewer)) -> list[dict]:
        return store.pending_actions()

    @app.post("/api/actions/{aid}/decision")
    def decision(aid: int, body: DecisionIn, who: str = Depends(reviewer)) -> dict:
        return wf(lambda: workflow.decide(aid, body.approve, body.comment, who))

    @app.post("/api/actions/{aid}/undo")
    def undo(aid: int, who: str = Depends(reviewer)) -> dict:
        return wf(lambda: workflow.undo(aid, who))

    @app.get("/api/audit")
    def audit_entries(ticket_id: int | None = None, who: str = Depends(reviewer)) -> list[dict]:
        with db.connection() as conn:
            return audit.entries(conn, ticket_id)

    @app.get("/api/audit/verify")
    def audit_verify(who: str = Depends(reviewer)) -> dict:
        with db.connection() as conn:
            return audit.verify(conn)

    @app.get("/api/metrics")
    def metrics() -> dict:
        return store.metrics()

    @app.get("/api/simulation")
    def simulation() -> dict:
        if not isinstance(directory, SimulatedDirectory):
            raise HTTPException(404, "Nur mit der simulierten Testumgebung verfügbar.")
        return directory.snapshot()

    @app.post("/api/simulation/reset")
    def simulation_reset(who: str = Depends(reviewer)) -> dict:
        if not isinstance(directory, SimulatedDirectory):
            raise HTTPException(404, "Nur mit der simulierten Testumgebung verfügbar.")
        reset_simulation(db)
        store.log(None, f"reviewer:{who}", "simulation_reset", {})
        return {"ok": True}

    if frontend_dir and (frontend_dir / "index.html").exists():
        app.mount("/assets", StaticFiles(directory=frontend_dir / "assets"), name="assets")

        @app.get("/", include_in_schema=False)
        def index() -> FileResponse:
            return FileResponse(frontend_dir / "index.html")

    return app
