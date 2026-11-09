"""Richtlinie: ordnet jede Aktion einer Risikostufe zu. Das Sprachmodell hat darauf keinen Einfluss.

Stufe 0: nur lesen, läuft automatisch.
Stufe 1: reversibel, läuft automatisch und wird protokolliert.
Stufe 2: kritisch, wird nur nach Freigabe durch einen Menschen ausgeführt.

Jede Regel kann eine Aktion nur HÖHER einstufen, nie niedriger.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .config import Settings
from .directory.base import Directory, DirectoryError
from .tools import ToolSpec


@dataclass(frozen=True)
class Risk:
    level: int
    reasons: tuple[str, ...] = field(default_factory=tuple)
    denied: bool = False        # Aktion wird gar nicht erst ausgeführt oder zur Freigabe vorgelegt

    @property
    def reason(self) -> str:
        return "; ".join(self.reasons)


@dataclass
class TicketState:
    requester_upn: str
    injection_flag: bool = False
    write_count: int = 0        # schreibende Aktionen in diesem Ticket (ausgeführt oder zur Freigabe vorgemerkt)


class Policy:
    def __init__(self, directory: Directory, settings: Settings):
        self.directory, self.s = directory, settings

    def assess(self, tool: ToolSpec, args: dict, state: TicketState) -> Risk:
        level, reasons = tool.base_level, []
        if tool.kind == "read":
            # Auskunftssperre: Daten über andere Personen bekommt weder der Agent noch der Antragsteller.
            if tool.target and (tool.target(args, self.directory) or "").lower() not in ("", state.requester_upn.lower()):
                return Risk(0, ("Auskunft über andere Personen gibt der Support nicht",), denied=True)
            return Risk(level, ("Nur lesend",))
        if tool.kind != "write":
            return Risk(level, ())
        reasons.append({1: "Reversible Aktion", 2: "Kritische Aktion (Rechte, Daten oder Löschen)"}[tool.base_level])

        def raise_to(new_level: int, why: str) -> None:
            nonlocal level
            level = max(level, new_level)
            reasons.append(why)

        if state.injection_flag:
            raise_to(2, "Ticket enthält Hinweise auf Prompt Injection")
        try:
            target = tool.target(args, self.directory) if tool.target else None
            if target and target.lower() != state.requester_upn.lower():
                raise_to(2, "Betrifft eine andere Person als den Antragsteller")
            if target and self.directory.is_privileged(target):
                raise_to(2, "Betrifft ein privilegiertes Konto")
            if tool.name == "unlock_account" and target:
                failures = self.directory.get_account_status(target)["failed_logons"]
                if failures >= self.s.lockout_suspicious_failures:
                    raise_to(2, f"Auffällig viele Fehlversuche ({failures}), möglicher Angriff")
            if tool.name == "add_group_member" and self.directory.get_group_info(args["group"])["privileged"]:
                raise_to(2, "Zielgruppe ist privilegiert")
        except DirectoryError:
            raise_to(2, "Ziel konnte nicht geprüft werden")
        if state.write_count >= self.s.max_write_actions_per_ticket:
            raise_to(2, f"Mehr als {self.s.max_write_actions_per_ticket} Änderungen in einem Ticket (Massenänderung)")
        return Risk(level, tuple(reasons))
