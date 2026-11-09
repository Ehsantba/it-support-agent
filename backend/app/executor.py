"""Führt eine geprüfte Aktion aus, merkt sich den Zustand davor (für Rückgängig) und prüft das Ergebnis danach."""
from __future__ import annotations

from .directory.base import Directory, DirectoryError
from .tools import ToolSpec


def execute(directory: Directory, tool: ToolSpec, args: dict) -> tuple[str, object, dict | None, str | None]:
    """Gibt (Status, Ergebnis, Undo-Info, Prüfergebnis) zurück. Status: EXECUTED oder FAILED."""
    undo_info = None
    if tool.capture:
        try:
            undo_info = tool.capture(directory, args)
        except DirectoryError:
            undo_info = None
    try:
        result = tool.run(directory, args)
    except DirectoryError as exc:
        return "FAILED", {"error": str(exc)}, None, None
    except Exception as exc:                                    # Fehler der Umgebung dürfen den Agenten nicht abstürzen lassen
        return "FAILED", {"error": f"Umgebung hat einen Fehler gemeldet ({type(exc).__name__})"}, None, None
    verification = None
    if tool.verify:
        try:
            ok, text = tool.verify(directory, args, result)
            verification = ("OK: " if ok else "FEHLGESCHLAGEN: ") + text
        except Exception:
            verification = "FEHLGESCHLAGEN: Prüfung war nicht möglich"
    return "EXECUTED", result, undo_info, verification
