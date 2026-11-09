"""Freigaben, Rückgängig-Machen und Abschlussprüfung durch Menschen."""
from __future__ import annotations

from datetime import datetime, timezone

from .directory.base import Directory, DirectoryError
from .executor import execute
from .store import Store
from .tools import ToolSpec


class WorkflowError(Exception):
    def __init__(self, message: str, status: int = 409):
        super().__init__(message)
        self.status = status


class Workflow:
    def __init__(self, store: Store, directory: Directory, tools: dict[str, ToolSpec]):
        self.store, self.directory, self.tools = store, directory, tools

    def decide(self, action_id: int, approve: bool, comment: str | None, reviewer: str) -> dict:
        action = self.store.get_action(action_id)
        if action is None:
            raise WorkflowError("Aktion nicht gefunden.", 404)
        if action["status"] != "PENDING_APPROVAL":
            raise WorkflowError("Die Aktion wartet nicht (mehr) auf eine Freigabe.")
        ticket = self.store.get_ticket(action["ticket_id"])
        if reviewer.lower() == ticket["requester_upn"].lower():
            raise WorkflowError("Vier-Augen-Prinzip: Der Antragsteller darf seine eigene Aktion nicht freigeben.", 403)
        comment = (comment or "").strip() or None
        if not approve:
            if not comment:
                raise WorkflowError("Bei einer Ablehnung ist ein Kommentar erforderlich.", 422)
            self.store.update_action(action_id, status="REJECTED", decided_by=reviewer, decision_comment=comment)
            self.store.log(ticket["id"], f"reviewer:{reviewer}", "action_rejected", {"action_id": action_id, "tool": action["tool"], "comment": comment})
        else:
            tool = self.tools.get(action["tool"])
            if tool is None:
                raise WorkflowError("Werkzeug existiert nicht mehr.", 422)
            status, result, undo_info, verification = execute(self.directory, tool, action["arguments"])
            self.store.update_action(action_id, status=status, result=result, undo_info=undo_info, verification=verification,
                                     decided_by=reviewer, decision_comment=comment)
            self.store.log(ticket["id"], f"reviewer:{reviewer}", "action_approved",
                           {"action_id": action_id, "tool": action["tool"], "arguments": action["arguments"], "comment": comment,
                            "status": status, "result": result, "verification": verification})
        self._after_decision(ticket["id"])
        return self.store.get_action(action_id)

    def _after_decision(self, ticket_id: int) -> None:
        ticket = self.store.get_ticket(ticket_id)
        if ticket["status"] == "AWAITING_APPROVAL" and not any(a["status"] == "PENDING_APPROVAL" for a in self.store.actions_of(ticket_id)):
            self.store.update_ticket(ticket_id, actor="system", event="all_approvals_decided", status="REVIEW")

    def undo(self, action_id: int, reviewer: str) -> dict:
        action = self.store.get_action(action_id)
        if action is None:
            raise WorkflowError("Aktion nicht gefunden.", 404)
        tool = self.tools.get(action["tool"])
        if action["status"] != "EXECUTED" or tool is None or tool.undo is None:
            raise WorkflowError("Diese Aktion lässt sich nicht rückgängig machen.")
        try:
            outcome = tool.undo(self.directory, action["arguments"], action["undo_info"])
        except DirectoryError as exc:
            raise WorkflowError(f"Rückgängig-Machen fehlgeschlagen: {exc}") from exc
        self.store.update_action(action_id, status="UNDONE")
        self.store.log(action["ticket_id"], f"reviewer:{reviewer}", "action_undone",
                       {"action_id": action_id, "tool": action["tool"], "arguments": action["arguments"], "result": outcome})
        return self.store.get_action(action_id)

    def review(self, ticket_id: int, verdict: str, comment: str | None, reviewer: str) -> dict:
        ticket = self.store.get_ticket(ticket_id)
        if ticket is None:
            raise WorkflowError("Ticket nicht gefunden.", 404)
        if ticket["status"] not in ("REVIEW", "ESCALATED", "NEEDS_INFO"):
            raise WorkflowError("Das Ticket ist noch nicht bereit für die Abschlussprüfung (offene Freigaben oder noch in Bearbeitung).")
        if any(a["status"] == "PENDING_APPROVAL" for a in self.store.actions_of(ticket_id)):
            raise WorkflowError("Es gibt noch offene Freigaben.")
        if reviewer.lower() == ticket["requester_upn"].lower():
            raise WorkflowError("Der Antragsteller darf sein eigenes Ticket nicht abschließen.", 403)
        comment = (comment or "").strip() or None
        if verdict == "reject" and not comment:
            raise WorkflowError("Bei einem negativen Urteil ist ein Kommentar erforderlich.", 422)
        self.store.update_ticket(ticket_id, actor=f"reviewer:{reviewer}", event="review_verdict",
                                 details={"verdict": verdict, "comment": comment}, status="CLOSED", review_verdict=verdict,
                                 review_comment=comment, reviewed_by=reviewer, reviewed_at=datetime.now(timezone.utc))
        return self.store.get_ticket(ticket_id)
