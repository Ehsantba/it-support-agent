"""Werkzeuge des Agenten: eine feste Allowlist mit Parameter-Schema, Risikostufe, Prüfung und Rückgängig-Funktion.

Was nicht in `build_tools()` steht, kann der Agent nicht aufrufen. Das Sprachmodell wählt nur Name und Argumente,
ausgeführt wird immer unser Code, nachdem Schema und Richtlinie geprüft sind.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Callable

from .directory.base import Directory

CATEGORIES = ["KONTOSPERRE", "PASSWORT", "GRUPPENRECHTE", "GERAET", "POSTFACH", "SONSTIGES"]

Target = Callable[[dict, Directory], "str | None"]


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    parameters: dict
    kind: str                       # meta | read | write
    base_level: int                 # 0 nur lesen, 1 reversibel, 2 kritisch (Freigabe nötig)
    run: Callable[[Directory, dict], object] | None = None
    target: Target | None = None    # betroffener Benutzer (für die Richtlinie)
    capture: Callable[[Directory, dict], dict | None] | None = None      # Zustand vor der Änderung
    verify: Callable[[Directory, dict, object], tuple[bool, str]] | None = None
    undo: Callable[[Directory, dict, dict | None], dict | None] | None = None


def _upn_schema(domains: tuple[str, ...]) -> dict:
    alt = "|".join(re.escape(d) for d in domains)
    return {"type": "string", "pattern": rf"[a-z0-9._%+\-]{{1,64}}@(?:{alt})", "maxLength": 120,
            "description": "Benutzerkonto (UPN), z. B. vorname.nachname@" + domains[0]}


_GROUP = {"type": "string", "pattern": r"[A-Za-z0-9ÄÖÜäöüß _.\-]{1,64}", "maxLength": 64, "description": "Name der Gruppe"}
_DEVICE = {"type": "string", "pattern": r"DEV-[0-9]{3,6}", "maxLength": 10, "description": "Geräte-ID, z. B. DEV-1001"}
_EMAIL = {"type": "string", "pattern": r"[A-Za-z0-9._%+\-]{1,64}@[A-Za-z0-9.\-]{1,100}\.[A-Za-z]{2,}", "maxLength": 160}


def _upn_target(args, d):
    return args.get("upn")


def _device_target(args, d):
    return d.get_device_owner(args["device_id"])


def build_tools(domains: tuple[str, ...]) -> dict[str, ToolSpec]:
    upn = _upn_schema(domains)
    T: list[ToolSpec] = [
        # ---- Meta ----
        ToolSpec("classify_ticket", "Ordnet das Ticket einer Kategorie zu. Immer der erste Schritt.",
                 {"type": "object", "required": ["category", "confidence", "summary"], "properties": {
                     "category": {"type": "string", "enum": CATEGORIES},
                     "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                     "summary": {"type": "string", "maxLength": 300}}}, "meta", 0),
        ToolSpec("finish", "Beendet die Bearbeitung. status: resolved (erledigt oder Freigabe beantragt), needs_info (Rückfrage nötig), escalate (an einen Menschen).",
                 {"type": "object", "required": ["status", "message", "reason"], "properties": {
                     "status": {"type": "string", "enum": ["resolved", "needs_info", "escalate"]},
                     "message": {"type": "string", "maxLength": 1500, "description": "Antwort an den Antragsteller"},
                     "reason": {"type": "string", "maxLength": 500, "description": "Kurze Begründung für das Review"}}}, "meta", 0),
        # ---- Stufe 0: lesen ----
        ToolSpec("get_account_status", "Status eines Kontos: aktiv, gesperrt, Fehlversuche, MFA.",
                 {"type": "object", "required": ["upn"], "properties": {"upn": upn}}, "read", 0,
                 run=lambda d, a: d.get_account_status(a["upn"]), target=_upn_target),
        ToolSpec("get_group_memberships", "Gruppen, in denen ein Benutzer Mitglied ist.",
                 {"type": "object", "required": ["upn"], "properties": {"upn": upn}}, "read", 0,
                 run=lambda d, a: d.get_group_memberships(a["upn"]), target=_upn_target),
        ToolSpec("get_group_info", "Beschreibung einer Gruppe und ob sie privilegiert ist.",
                 {"type": "object", "required": ["group"], "properties": {"group": _GROUP}}, "read", 0,
                 run=lambda d, a: d.get_group_info(a["group"])),
        ToolSpec("get_device_status", "Geräte eines Benutzers mit Konformitätsstatus.",
                 {"type": "object", "required": ["upn"], "properties": {"upn": upn}}, "read", 0,
                 run=lambda d, a: d.get_device_status(a["upn"]), target=_upn_target),
        ToolSpec("get_mailbox_info", "Postfachgröße, Auslastung und Weiterleitung.",
                 {"type": "object", "required": ["upn"], "properties": {"upn": upn}}, "read", 0,
                 run=lambda d, a: d.get_mailbox_info(a["upn"]), target=_upn_target),
        # ---- Stufe 1: reversibel ----
        ToolSpec("unlock_account", "Entsperrt ein gesperrtes Konto.",
                 {"type": "object", "required": ["upn"], "properties": {"upn": upn}}, "write", 1,
                 run=lambda d, a: d.unlock_account(a["upn"]), target=_upn_target,
                 capture=lambda d, a: {"locked_out": d.get_account_status(a["upn"])["locked_out"]},
                 verify=lambda d, a, r: (not d.get_account_status(a["upn"])["locked_out"], "Konto ist nicht mehr gesperrt"),
                 undo=lambda d, a, info: d.lock_account(a["upn"]) if info and info.get("locked_out") else None),
        ToolSpec("send_password_reset_link", "Schickt einen Link zum Zurücksetzen des Passworts an die registrierte zweite Authentifizierung. Das Passwort selbst wird nie ausgegeben.",
                 {"type": "object", "required": ["upn"], "properties": {"upn": upn}}, "write", 1,
                 run=lambda d, a: d.send_password_reset_link(a["upn"]), target=_upn_target,
                 verify=lambda d, a, r: (bool(isinstance(r, dict) and r.get("sent")), "Versand vom System bestätigt (Zustellung nicht prüfbar)")),
        ToolSpec("trigger_device_sync", "Stößt die Synchronisierung eines verwalteten Geräts an.",
                 {"type": "object", "required": ["device_id"], "properties": {"device_id": _DEVICE}}, "write", 1,
                 run=lambda d, a: d.trigger_device_sync(a["device_id"]), target=_device_target,
                 verify=lambda d, a, r: _verify_device_compliant(d, a)),
        # ---- Stufe 2: kritisch ----
        ToolSpec("add_group_member", "Fügt einen Benutzer einer Gruppe hinzu (ändert Rechte).",
                 {"type": "object", "required": ["upn", "group"], "properties": {"upn": upn, "group": _GROUP}}, "write", 2,
                 run=lambda d, a: d.add_group_member(a["upn"], a["group"]), target=_upn_target,
                 capture=lambda d, a: {"was_member": a["group"] in d.get_group_memberships(a["upn"])},
                 verify=lambda d, a, r: (a["group"] in d.get_group_memberships(a["upn"]), "Mitgliedschaft vorhanden"),
                 undo=lambda d, a, info: d.remove_group_member(a["upn"], a["group"]) if info and not info.get("was_member") else None),
        ToolSpec("set_mailbox_forwarding", "Richtet eine Weiterleitung für ein Postfach ein (Daten können das Unternehmen verlassen).",
                 {"type": "object", "required": ["upn", "address"], "properties": {"upn": upn, "address": _EMAIL}}, "write", 2,
                 run=lambda d, a: d.set_mailbox_forwarding(a["upn"], a["address"]), target=_upn_target,
                 capture=lambda d, a: {"previous": d.get_mailbox_info(a["upn"])["forwarding_address"]},
                 verify=lambda d, a, r: (d.get_mailbox_info(a["upn"])["forwarding_address"] == a["address"], "Weiterleitung eingetragen"),
                 undo=lambda d, a, info: d.set_mailbox_forwarding(a["upn"], (info or {}).get("previous"))),
        ToolSpec("wipe_device", "Löscht alle Daten auf einem Gerät (nicht rückgängig zu machen).",
                 {"type": "object", "required": ["device_id"], "properties": {"device_id": _DEVICE}}, "write", 2,
                 run=lambda d, a: d.wipe_device(a["device_id"]), target=_device_target,
                 verify=lambda d, a, r: (any(x["wipe_pending"] for x in d.get_device_status(d.get_device_owner(a["device_id"])) if x["device_id"] == a["device_id"]), "Löschbefehl vorgemerkt")),
        ToolSpec("disable_account", "Deaktiviert ein Benutzerkonto.",
                 {"type": "object", "required": ["upn"], "properties": {"upn": upn}}, "write", 2,
                 run=lambda d, a: d.disable_account(a["upn"]), target=_upn_target,
                 capture=lambda d, a: {"enabled": d.get_account_status(a["upn"])["enabled"]},
                 verify=lambda d, a, r: (not d.get_account_status(a["upn"])["enabled"], "Konto ist deaktiviert"),
                 undo=lambda d, a, info: d.enable_account(a["upn"]) if info and info.get("enabled") else None),
    ]
    return {t.name: t for t in T}


def _verify_device_compliant(d: Directory, a: dict) -> tuple[bool, str]:
    owner = d.get_device_owner(a["device_id"])
    dev = next((x for x in d.get_device_status(owner) if x["device_id"] == a["device_id"]), None)
    if dev and dev["compliance"] == "compliant":
        return True, "Gerät ist nach der Synchronisierung konform"
    return False, "Gerät ist weiterhin nicht konform"


def tool_catalog(tools: dict[str, ToolSpec]) -> str:
    """Beschreibung für den Prompt (Name, Stufe, Parameter)."""
    lines = []
    for t in tools.values():
        params = ", ".join(f"{k}{'' if k in t.parameters.get('required', []) else '?'}" for k in t.parameters["properties"])
        enum = {k: v["enum"] for k, v in t.parameters["properties"].items() if "enum" in v}
        extra = f" Erlaubte Werte: {enum}" if enum else ""
        lines.append(f"- {t.name}({params}) [Stufe {t.base_level}]: {t.description}{extra}")
    return "\n".join(lines)
