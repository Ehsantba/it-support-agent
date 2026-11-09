from __future__ import annotations

from ..config import Settings
from ..db import Database
from .base import Directory
from .simulated import SimulatedDirectory


def build_directory(settings: Settings, db: Database) -> Directory:
    if settings.directory_backend == "simulated":
        return SimulatedDirectory(db)
    if settings.directory_backend == "graph_powershell":
        from .composite import build_real_directory
        return build_real_directory()
    raise ValueError(f"Unbekanntes DIRECTORY_BACKEND: {settings.directory_backend}")
