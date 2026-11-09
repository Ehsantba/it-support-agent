"""Prüft den Zugang zum Sprachmodell mit einer einzigen kleinen Anfrage.

    cd backend && python scripts/check_llm.py

Liest die Einstellungen aus ../.env (siehe .env.example). Es werden nur erfundene Beispieldaten gesendet.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from app.config import Settings                     # noqa: E402
from app.envfile import load_env                     # noqa: E402
from app.llm import LLMError, build_llm              # noqa: E402
from app.llm.prompts import ParseError, build_system, build_user, parse_action  # noqa: E402
from app.tools import build_tools, tool_catalog      # noqa: E402

load_env(ROOT / ".env")
settings = Settings.from_env()
print(f"Anbieter: {settings.llm_provider}, Modell: {settings.llm_model}")
try:
    llm = build_llm(settings)
    system = build_system(tool_catalog(build_tools(settings.allowed_domains)))
    user = build_user("anna.becker@example.test", "Konto gesperrt", "Ich bin aus meinem Konto ausgesperrt.", [])
    raw = llm.complete(system, user)
    print("Rohantwort:", raw[:300].replace("\n", " "))
    thought, tool, args = parse_action(raw)
    print(f"\nOK: Das Modell antwortet im erwarteten Format. Gewählt: {tool} {args}")
    if tool != "classify_ticket":
        print("Hinweis: Erwartet wurde classify_ticket als erster Schritt. Das ist kein Fehler des Zugangs, aber ein schlechtes Zeichen für dieses Modell.")
except (LLMError, ParseError) as exc:
    print(f"\nFEHLER: {exc}")
    sys.exit(1)
