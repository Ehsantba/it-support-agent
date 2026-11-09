"""Speichern von Tickets und Aktionen. Jede Änderung schreibt in derselben Transaktion das Audit-Log."""
from __future__ import annotations

from psycopg.types.json import Jsonb

from . import audit
from .db import Database

TICKET_FIELDS = {"status", "category", "confidence", "summary", "injection_flag", "agent_status", "final_message",
                 "llm_name", "steps_used", "error", "review_verdict", "review_comment", "reviewed_by", "reviewed_at",
                 "started_at", "finished_at"}


def _iso(row: dict | None) -> dict | None:
    if row is None:
        return None
    return {k: (v.isoformat() if hasattr(v, "isoformat") else v) for k, v in row.items()}


class Store:
    def __init__(self, db: Database):
        self.db = db

    # ---- Tickets ----
    def create_ticket(self, requester_upn: str, subject: str, body: str, dry_run: bool = False) -> int:
        with self.db.connection() as conn:
            with conn.transaction():
                tid = conn.execute("INSERT INTO tickets (requester_upn, subject, body, dry_run) VALUES (%s,%s,%s,%s) RETURNING id",
                                   (requester_upn.lower(), subject, body, dry_run)).fetchone()["id"]
                audit.write(conn, tid, f"user:{requester_upn.lower()}", "ticket_created", {"subject": subject, "dry_run": dry_run})
        return tid

    def get_ticket(self, tid: int) -> dict | None:
        with self.db.connection() as conn:
            return _iso(conn.execute("SELECT * FROM tickets WHERE id = %s", (tid,)).fetchone())

    def list_tickets(self, status: str | None = None, limit: int = 100) -> list[dict]:
        with self.db.connection() as conn:
            if status:
                rows = conn.execute("SELECT * FROM tickets WHERE status = %s ORDER BY id DESC LIMIT %s", (status, limit)).fetchall()
            else:
                rows = conn.execute("SELECT * FROM tickets ORDER BY id DESC LIMIT %s", (limit,)).fetchall()
        return [_iso(r) for r in rows]

    def update_ticket(self, tid: int, actor: str | None = None, event: str | None = None, details: dict | None = None, **fields) -> None:
        bad = set(fields) - TICKET_FIELDS
        if bad:
            raise ValueError(f"Unbekannte Felder: {bad}")
        sets = ", ".join(f"{k} = %s" for k in fields)
        with self.db.connection() as conn:
            with conn.transaction():
                if fields:
                    conn.execute(f"UPDATE tickets SET {sets} WHERE id = %s", (*fields.values(), tid))   # Spaltennamen aus fester Liste
                if event:
                    audit.write(conn, tid, actor or "system", event, details)

    # ---- Aktionen ----
    def add_action(self, tid: int, step: int, tool: str, arguments: dict, level: int, reason: str, status: str,
                   result=None, undo_info=None, verification: str | None = None) -> int:
        with self.db.connection() as conn:
            return conn.execute(
                "INSERT INTO actions (ticket_id, step, tool, arguments, risk_level, risk_reason, status, result, undo_info, verification, executed_at) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s, CASE WHEN %s IN ('EXECUTED','DRY_RUN') THEN now() END) RETURNING id",
                (tid, step, tool, Jsonb(arguments), level, reason, status, Jsonb(result), Jsonb(undo_info), verification, status)).fetchone()["id"]

    def update_action(self, aid: int, **fields) -> None:
        allowed = {"status", "result", "undo_info", "verification", "decided_by", "decision_comment"}
        bad = set(fields) - allowed
        if bad:
            raise ValueError(f"Unbekannte Felder: {bad}")
        values = [Jsonb(v) if k in ("result", "undo_info") else v for k, v in fields.items()]
        sets = ", ".join(f"{k} = %s" for k in fields)
        with self.db.connection() as conn:
            conn.execute(f"UPDATE actions SET {sets}, executed_at = CASE WHEN %s = 'EXECUTED' THEN now() ELSE executed_at END WHERE id = %s",
                         (*values, fields.get("status"), aid))

    def get_action(self, aid: int) -> dict | None:
        with self.db.connection() as conn:
            return _iso(conn.execute("SELECT * FROM actions WHERE id = %s", (aid,)).fetchone())

    def actions_of(self, tid: int) -> list[dict]:
        with self.db.connection() as conn:
            return [_iso(r) for r in conn.execute("SELECT * FROM actions WHERE ticket_id = %s ORDER BY id", (tid,)).fetchall()]

    def pending_actions(self) -> list[dict]:
        with self.db.connection() as conn:
            rows = conn.execute("SELECT a.*, t.subject, t.requester_upn FROM actions a JOIN tickets t ON t.id = a.ticket_id "
                                "WHERE a.status = 'PENDING_APPROVAL' ORDER BY a.id").fetchall()
        return [_iso(r) for r in rows]

    def log(self, tid: int | None, actor: str, event: str, details: dict | None = None) -> None:
        with self.db.connection() as conn:
            with conn.transaction():
                audit.write(conn, tid, actor, event, details)

    def metrics(self) -> dict:
        with self.db.connection() as conn:
            by_status = {r["status"]: r["n"] for r in conn.execute("SELECT status, count(*) AS n FROM tickets GROUP BY status")}
            avg = conn.execute("SELECT avg(extract(epoch FROM (finished_at - started_at))) AS s FROM tickets WHERE finished_at IS NOT NULL AND started_at IS NOT NULL").fetchone()["s"]
            act = {r["status"]: r["n"] for r in conn.execute("SELECT status, count(*) AS n FROM actions GROUP BY status")}
            verdicts = {(r["review_verdict"] or "offen"): r["n"] for r in conn.execute("SELECT review_verdict, count(*) AS n FROM tickets GROUP BY review_verdict")}
        total = sum(by_status.values())
        approved, rejected = verdicts.get("approve", 0), verdicts.get("reject", 0)
        return {"tickets": total, "by_status": by_status, "avg_seconds": round(float(avg), 2) if avg is not None else None,
                "actions_by_status": act, "reviews": {"approved": approved, "rejected": rejected,
                "approval_rate": round(approved / (approved + rejected), 2) if approved + rejected else None}}
