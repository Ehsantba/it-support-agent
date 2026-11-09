from __future__ import annotations

from typing import Protocol


class LLMError(Exception):
    """Sprachmodell nicht erreichbar, Zugangsdaten fehlen oder unbrauchbare Antwort."""


class LLM(Protocol):
    name: str

    def complete(self, system: str, user: str) -> str:
        """Gibt den Rohtext der Modellantwort zurück (erwartet wird ein JSON-Objekt)."""
