"""Startpunkt: uvicorn app.main:app"""
from __future__ import annotations

from pathlib import Path

from .agent import Agent
from .api import create_app
from .config import Settings
from .db import Database
from .directory.factory import build_directory
from .directory.simulated import seed_if_empty
from .llm import build_llm
from .store import Store


def build_app():
    settings = Settings.from_env()
    db = Database(settings.database_url, settings.schema).init()
    directory = build_directory(settings, db)
    if settings.directory_backend == "simulated" and settings.seed_simulation:
        seed_if_empty(db)
    agent = Agent(Store(db), directory, build_llm(settings), settings)
    frontend = Path(__file__).resolve().parents[2] / "frontend" / "dist"
    return create_app(settings, db, directory, agent, frontend)


app = build_app()
