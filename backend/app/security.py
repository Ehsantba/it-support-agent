"""Erkennung typischer Prompt-Injection-Muster im Ticket-Text. Das ist nur ein zusätzlicher Hinweis:
Die eigentliche Absicherung sind Allowlist, Schema, Richtlinie und Freigabe, nicht die Mustererkennung."""
from __future__ import annotations

import re

PATTERNS = {
    "ignore_instructions": r"ignorier\w*\s+(alle\s+|die\s+|deine\s+|vorherige\w*\s+)*(anweisung|regel|vorgabe|instruktion)|ignore\s+(all\s+|previous\s+|the\s+)*(instruction|rule)",
    "forget_rules": r"vergiss\s+(alle\s+|deine\s+|die\s+)*(regel|anweisung|vorgabe)",
    "role_change": r"du\s+bist\s+(jetzt|nun|ab\s+sofort)|you\s+are\s+now|handle\s+als|act\s+as",
    "system_prompt": r"system\s*-?\s*(prompt|nachricht|message)|systemanweisung",
    "new_instructions": r"neue\s+anweisung|new\s+instruction|override|developer\s+mode|jailbreak",
    "delimiter_break": r"</?\s*(ticket|verlauf|system)\s*>|```",
    "claims_authority": r"(ich\s+bin|i\s+am)\s+(der\s+|die\s+)?(admin|administrator|geschäftsführer|ceo|cio|it-leiter)|im\s+auftrag\s+der\s+(geschäftsführung|it-leitung)",
}
_COMPILED = {k: re.compile(v, re.IGNORECASE) for k, v in PATTERNS.items()}


def scan_ticket_text(text: str) -> list[str]:
    return [name for name, rx in _COMPILED.items() if rx.search(text)]
