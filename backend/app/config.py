"""Einstellungen aus Umgebungsvariablen."""
from __future__ import annotations

import os
from dataclasses import dataclass, field

GEMINI_OPENAI_BASE = "https://generativelanguage.googleapis.com/v1beta/openai"


@dataclass(frozen=True)
class Settings:
    database_url: str = "postgresql://agent:agent@localhost:5432/agent"
    schema: str = "public"

    # Sprachmodell: mock (regelbasiert, für Tests), gemini, openai_compat (jeder OpenAI-kompatible Endpunkt)
    llm_provider: str = "mock"
    llm_base_url: str | None = None
    llm_api_key: str | None = None
    llm_model: str = "gemini-2.5-flash"
    llm_min_interval: float = 0.0    # Sekunden Pause zwischen Aufrufen (Gratis-Kontingente haben enge Limits)

    # Verzeichnis-Backend: simulated (Testumgebung in der Datenbank), graph_powershell (echte Umgebung)
    directory_backend: str = "simulated"
    allowed_domains: tuple[str, ...] = ("example.test",)

    dry_run: bool = False            # Schreibende Aktionen nur beschreiben, nicht ausführen
    max_steps: int = 8
    max_write_actions_per_ticket: int = 3
    lockout_suspicious_failures: int = 20
    auto_process: bool = True        # neue Tickets sofort im Hintergrund bearbeiten
    reviewer_token: str = "demo-reviewer"
    seed_simulation: bool = True

    @classmethod
    def from_env(cls) -> "Settings":
        e, d = os.environ, cls()
        provider = e.get("LLM_PROVIDER", d.llm_provider).lower()
        key = e.get("LLM_API_KEY") or e.get("GEMINI_API_KEY") or None
        base = e.get("LLM_BASE_URL") or (GEMINI_OPENAI_BASE if provider == "gemini" else None)
        return cls(
            database_url=e.get("DATABASE_URL", d.database_url),
            schema=e.get("DB_SCHEMA", d.schema),
            llm_provider=provider, llm_base_url=base, llm_api_key=key,
            llm_model=e.get("LLM_MODEL", d.llm_model),
            llm_min_interval=float(e.get("LLM_MIN_INTERVAL", d.llm_min_interval)),
            directory_backend=e.get("DIRECTORY_BACKEND", d.directory_backend),
            allowed_domains=tuple(x.strip().lower() for x in e.get("ALLOWED_DOMAINS", "example.test").split(",") if x.strip()),
            dry_run=e.get("DRY_RUN", "false").lower() == "true",
            max_steps=int(e.get("MAX_STEPS", d.max_steps)),
            auto_process=e.get("AUTO_PROCESS", "true").lower() == "true",
            reviewer_token=e.get("REVIEWER_TOKEN", d.reviewer_token),
            seed_simulation=e.get("SEED_SIMULATION", "true").lower() == "true",
        )
