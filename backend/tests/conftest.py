"""Gemeinsame Test-Hilfen. Tests laufen gegen eine echte PostgreSQL (TEST_DATABASE_URL), ein eigenes Schema pro Testlauf."""
from __future__ import annotations

import os
import uuid
from dataclasses import replace

import pytest

from app.agent import Agent
from app.config import Settings
from app.db import Database
from app.directory.simulated import SimulatedDirectory, reset_simulation
from app.llm.mock import MockLLM
from app.store import Store
from app.tools import build_tools

URL = os.environ.get("TEST_DATABASE_URL", "postgresql://rag:rag@localhost:5432/rag_test")


class ScriptedLLM:
    """Liefert vorgegebene Antworten der Reihe nach und merkt sich die Prompts."""
    name = "scripted"

    def __init__(self, responses):
        self.responses, self.calls = list(responses), []

    def complete(self, system, user):
        self.calls.append((system, user))
        if not self.responses:
            raise AssertionError("ScriptedLLM: keine Antwort mehr vorgesehen")
        r = self.responses.pop(0)
        if isinstance(r, Exception):
            raise r
        return r


@pytest.fixture(scope="session")
def settings():
    return Settings(database_url=URL, schema=f"t_{uuid.uuid4().hex[:10]}", auto_process=False)


@pytest.fixture(scope="session")
def database(settings):
    db = Database(settings.database_url, settings.schema).init()
    yield db
    db.close()
    db.drop_schema()


@pytest.fixture(autouse=True)
def clean(database):
    with database.connection() as conn:
        conn.execute("TRUNCATE audit_log, actions, tickets RESTART IDENTITY CASCADE")
    reset_simulation(database)
    yield


@pytest.fixture()
def store(database):
    return Store(database)


@pytest.fixture()
def directory(database):
    return SimulatedDirectory(database)


@pytest.fixture()
def tools(settings):
    return build_tools(settings.allowed_domains)


@pytest.fixture()
def make_agent(store, directory, settings):
    def factory(llm=None, **overrides):
        return Agent(store, directory, llm or MockLLM(), replace(settings, **overrides))
    return factory


@pytest.fixture()
def run_ticket(store, make_agent):
    def run(requester, subject, body, llm=None, dry_run=False, **overrides):
        agent = make_agent(llm, **overrides)
        tid = store.create_ticket(requester, subject, body, dry_run)
        agent.process(tid)
        return tid, agent
    return run
