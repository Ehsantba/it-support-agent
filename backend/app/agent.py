"""Agent-Schleife: verstehen, planen, Werkzeug aufrufen, Ergebnis prüfen, antworten.

Das Sprachmodell schlägt pro Schritt genau eine Aktion vor. Ausgeführt wird sie nur, wenn Allowlist, Parameter-Schema und
Richtlinie es erlauben. Kritische Aktionen (Stufe 2) werden nie ausgeführt, sondern einem Menschen zur Freigabe vorgelegt.
"""
from __future__ import annotations

from datetime import datetime, timezone

from .config import Settings
from .directory.base import Directory
from .executor import execute
from .llm.base import LLM, LLMError
from .llm.prompts import ParseError, build_system, build_user, format_step, parse_action
from .policy import Policy, TicketState
from .schema import validate
from .security import scan_ticket_text
from .store import Store
from .tools import ToolSpec, build_tools, tool_catalog

MAX_INVALID = 3


class Agent:
    def __init__(self, store: Store, directory: Directory, llm: LLM, settings: Settings,
                 tools: dict[str, ToolSpec] | None = None):
        self.store, self.directory, self.llm, self.s = store, directory, llm, settings
        self.tools = tools or build_tools(settings.allowed_domains)
        self.policy = Policy(directory, settings)
        self.system = build_system(tool_catalog(self.tools))

    # ------------------------------------------------------------------
    def process(self, ticket_id: int) -> dict:
        ticket = self.store.get_ticket(ticket_id)
        if ticket is None or ticket["status"] not in ("NEW", "FAILED"):
            return ticket or {}
        flags = scan_ticket_text(ticket["subject"] + "\n" + ticket["body"])
        self.store.update_ticket(
            ticket_id, actor="agent", event="processing_started",
            details={"llm": self.llm.name, "injection_patterns": flags, "dry_run": self.s.dry_run or ticket["dry_run"]},
            status="PROCESSING", llm_name=self.llm.name, injection_flag=bool(flags), error=None,
            started_at=datetime.now(timezone.utc), finished_at=None)
        dry_run = self.s.dry_run or ticket["dry_run"]
        state = TicketState(ticket["requester_upn"], injection_flag=bool(flags))
        history: list[str] = []
        invalid, finish = 0, None
        step = 0
        try:
            while step < self.s.max_steps and finish is None:
                step += 1
                prompt = build_user(ticket["requester_upn"], ticket["subject"], ticket["body"], history, bool(flags))
                thought, tool_name, args = self._ask(ticket_id, prompt)
                self.store.log(ticket_id, "agent", "llm_decision", {"step": step, "tool": tool_name, "arguments": args, "thought": thought})
                line, finish, was_invalid = self._handle(ticket_id, step, tool_name, args, state, dry_run)
                history.append(line)
                invalid += was_invalid
                if invalid >= MAX_INVALID:
                    finish = {"status": "escalate", "message": "Der Agent konnte keine gültige Aktion bestimmen. Ein Kollege übernimmt.",
                              "reason": f"{MAX_INVALID} ungültige oder abgelehnte Schritte"}
                    self.store.log(ticket_id, "system", "too_many_invalid_steps", {"count": invalid})
        except (LLMError, ParseError) as exc:
            self.store.update_ticket(ticket_id, actor="system", event="agent_failed", details={"error": str(exc)},
                                     status="FAILED", error=str(exc)[:500], steps_used=step, finished_at=datetime.now(timezone.utc))
            return self.store.get_ticket(ticket_id)

        if finish is None:
            finish = {"status": "escalate", "message": "Die Bearbeitung wurde nach der maximalen Schrittzahl abgebrochen. Ein Kollege übernimmt.",
                      "reason": f"Schrittlimit ({self.s.max_steps}) erreicht"}
            self.store.log(ticket_id, "system", "step_limit_reached", {"max_steps": self.s.max_steps})
        return self._finalise(ticket_id, finish, step)

    # ------------------------------------------------------------------
    def _ask(self, ticket_id: int, prompt: str) -> tuple[str, str, dict]:
        """Fragt das Modell. Bei ungültiger Antwort gibt es genau einen zweiten Versuch mit Hinweis."""
        raw = self.llm.complete(self.system, prompt)
        try:
            return parse_action(raw)
        except ParseError as first:
            self.store.log(ticket_id, "agent", "llm_invalid_output", {"error": str(first), "raw": raw[:500]})
            retry = prompt + f"\n\nDeine letzte Antwort war ungültig ({first}). Antworte NUR mit dem JSON-Objekt."
            return parse_action(self.llm.complete(self.system, retry))

    def _handle(self, tid: int, step: int, name: str, args: dict, state: TicketState, dry_run: bool):
        """Gibt (Verlaufszeile, finish-Dict oder None, 1 falls ungültig) zurück."""
        tool = self.tools.get(name)
        if tool is None:
            return self._blocked(tid, step, name, args, "Werkzeug ist nicht in der Allowlist"), None, 1
        errors = validate(args, tool.parameters)
        if errors:
            return self._blocked(tid, step, name, args, "Ungültige Argumente: " + "; ".join(errors)), None, 1

        if name == "classify_ticket":
            self.store.update_ticket(tid, actor="agent", event="classified", details=args, category=args["category"],
                                     confidence=args["confidence"], summary=args["summary"])
            self.store.add_action(tid, step, name, args, 0, "", "EXECUTED", result={"ok": True})
            return format_step(step, name, args, "EXECUTED", {"ok": True}, None), None, 0
        if name == "finish":
            self.store.add_action(tid, step, name, args, 0, "", "EXECUTED", result={"ok": True})
            return format_step(step, name, args, "EXECUTED", {"ok": True}, None), dict(args), 0

        risk = self.policy.assess(tool, args, state)
        self.store.log(tid, "policy", "risk_assessed", {"step": step, "tool": name, "level": risk.level, "reasons": list(risk.reasons)})
        if risk.denied:
            result = {"error": risk.reason}
            self.store.add_action(tid, step, name, args, 0, risk.reason, "BLOCKED", result=result)
            self.store.log(tid, "policy", "action_denied", {"step": step, "tool": name, "arguments": args, "reason": risk.reason})
            return format_step(step, name, args, "BLOCKED", result, None), None, 0
        if tool.kind == "write":
            state.write_count += 1

        if risk.level >= 2:
            result = {"hinweis": "Diese Aktion ist kritisch und wartet auf die Freigabe durch einen Menschen.", "grund": risk.reason}
            aid = self.store.add_action(tid, step, name, args, risk.level, risk.reason, "PENDING_APPROVAL", result=result)
            self.store.log(tid, "policy", "approval_requested", {"action_id": aid, "tool": name, "arguments": args, "reason": risk.reason})
            return format_step(step, name, args, "PENDING_APPROVAL", result, None), None, 0
        if tool.kind == "write" and dry_run:
            result = {"dry_run": True, "beschreibung": f"{name} mit {args} wäre ausgeführt worden."}
            self.store.add_action(tid, step, name, args, risk.level, risk.reason, "DRY_RUN", result=result)
            self.store.log(tid, "agent", "dry_run", {"tool": name, "arguments": args})
            return format_step(step, name, args, "DRY_RUN", result, None), None, 0

        status, result, undo_info, verification = execute(self.directory, tool, args)
        aid = self.store.add_action(tid, step, name, args, risk.level, risk.reason, status, result=result,
                                    undo_info=undo_info, verification=verification)
        self.store.log(tid, "agent", "action_executed" if status == "EXECUTED" else "action_failed",
                       {"action_id": aid, "tool": name, "arguments": args, "level": risk.level, "result": result, "verification": verification})
        return format_step(step, name, args, status, result, verification), None, 0

    def _blocked(self, tid: int, step: int, name: str, args: dict, reason: str) -> str:
        result = {"error": reason}
        self.store.add_action(tid, step, name[:80], args if isinstance(args, dict) else {}, 0, reason, "BLOCKED", result=result)
        self.store.log(tid, "policy", "action_blocked", {"step": step, "tool": name[:80], "arguments": args, "reason": reason})
        return format_step(step, name[:80], args if isinstance(args, dict) else {}, "BLOCKED", result, None)

    def _finalise(self, tid: int, finish: dict, steps: int) -> dict:
        pending = any(a["status"] == "PENDING_APPROVAL" for a in self.store.actions_of(tid))
        agent_status = finish["status"]
        if agent_status == "escalate":
            status = "ESCALATED"
        elif agent_status == "needs_info":
            status = "NEEDS_INFO"
        else:
            status = "AWAITING_APPROVAL" if pending else "REVIEW"
        self.store.update_ticket(tid, actor="agent", event="processing_finished",
                                 details={"status": status, "agent_status": agent_status, "reason": finish["reason"], "pending_approvals": pending},
                                 status=status, agent_status=agent_status, final_message=finish["message"], steps_used=steps,
                                 finished_at=datetime.now(timezone.utc))
        return self.store.get_ticket(tid)
