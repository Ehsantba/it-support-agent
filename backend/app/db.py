"""Datenbank: Verbindungspool und Schema (PostgreSQL)."""
from __future__ import annotations

import re
from contextlib import contextmanager

import psycopg
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

_SCHEMA_RE = re.compile(r"^[a-z][a-z0-9_]{0,62}$")

DDL = [
    """CREATE TABLE IF NOT EXISTS tickets (
        id BIGSERIAL PRIMARY KEY,
        requester_upn TEXT NOT NULL,
        subject TEXT NOT NULL,
        body TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'NEW',
        category TEXT,
        confidence REAL,
        summary TEXT,
        injection_flag BOOLEAN NOT NULL DEFAULT FALSE,
        dry_run BOOLEAN NOT NULL DEFAULT FALSE,
        agent_status TEXT,
        final_message TEXT,
        llm_name TEXT,
        steps_used INT NOT NULL DEFAULT 0,
        error TEXT,
        review_verdict TEXT,
        review_comment TEXT,
        reviewed_by TEXT,
        reviewed_at TIMESTAMPTZ,
        created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        started_at TIMESTAMPTZ,
        finished_at TIMESTAMPTZ
    )""",
    """CREATE TABLE IF NOT EXISTS actions (
        id BIGSERIAL PRIMARY KEY,
        ticket_id BIGINT NOT NULL REFERENCES tickets (id) ON DELETE CASCADE,
        step INT NOT NULL,
        tool TEXT NOT NULL,
        arguments JSONB NOT NULL DEFAULT '{}',
        risk_level INT NOT NULL DEFAULT 0,
        risk_reason TEXT,
        status TEXT NOT NULL,
        result JSONB,
        undo_info JSONB,
        verification TEXT,
        decided_by TEXT,
        decision_comment TEXT,
        created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        executed_at TIMESTAMPTZ
    )""",
    "CREATE INDEX IF NOT EXISTS actions_ticket_idx ON actions (ticket_id)",
    "CREATE INDEX IF NOT EXISTS actions_status_idx ON actions (status)",
    """CREATE TABLE IF NOT EXISTS audit_log (
        id BIGSERIAL PRIMARY KEY,
        ts TIMESTAMPTZ NOT NULL,
        ts_text TEXT NOT NULL,
        ticket_id BIGINT,
        actor TEXT NOT NULL,
        event TEXT NOT NULL,
        details JSONB NOT NULL,
        prev_hash TEXT NOT NULL,
        hash TEXT NOT NULL
    )""",
    "CREATE INDEX IF NOT EXISTS audit_ticket_idx ON audit_log (ticket_id)",
    # ---- Simulierte Testumgebung (Verzeichnis, Geräteverwaltung, Postfächer) ----
    """CREATE TABLE IF NOT EXISTS sim_users (
        upn TEXT PRIMARY KEY, display_name TEXT NOT NULL, department TEXT NOT NULL,
        account_enabled BOOLEAN NOT NULL DEFAULT TRUE, locked_out BOOLEAN NOT NULL DEFAULT FALSE,
        failed_logons INT NOT NULL DEFAULT 0, password_last_set DATE, mfa_registered BOOLEAN NOT NULL DEFAULT TRUE
    )""",
    "CREATE TABLE IF NOT EXISTS sim_groups (name TEXT PRIMARY KEY, description TEXT NOT NULL, privileged BOOLEAN NOT NULL DEFAULT FALSE)",
    "CREATE TABLE IF NOT EXISTS sim_memberships (upn TEXT NOT NULL REFERENCES sim_users (upn) ON DELETE CASCADE, group_name TEXT NOT NULL REFERENCES sim_groups (name) ON DELETE CASCADE, PRIMARY KEY (upn, group_name))",
    """CREATE TABLE IF NOT EXISTS sim_devices (
        device_id TEXT PRIMARY KEY, upn TEXT NOT NULL REFERENCES sim_users (upn) ON DELETE CASCADE,
        name TEXT NOT NULL, os TEXT NOT NULL, compliance TEXT NOT NULL, last_sync TIMESTAMPTZ,
        encrypted BOOLEAN NOT NULL DEFAULT TRUE, sync_fixes BOOLEAN NOT NULL DEFAULT FALSE, wipe_pending BOOLEAN NOT NULL DEFAULT FALSE
    )""",
    """CREATE TABLE IF NOT EXISTS sim_mailboxes (
        upn TEXT PRIMARY KEY REFERENCES sim_users (upn) ON DELETE CASCADE,
        quota_mb INT NOT NULL, used_mb INT NOT NULL, forwarding_address TEXT
    )""",
]


class Database:
    def __init__(self, url: str, schema: str = "public"):
        if not _SCHEMA_RE.match(schema):
            raise ValueError("Ungültiger Schema-Name")
        self.url, self.schema = url, schema
        self.pool: ConnectionPool | None = None

    def init(self) -> "Database":
        with psycopg.connect(self.url, autocommit=True) as conn:
            conn.execute(f'CREATE SCHEMA IF NOT EXISTS "{self.schema}"')
            conn.execute(f'SET search_path TO "{self.schema}"')
            for statement in DDL:
                conn.execute(statement)

        def configure(conn: psycopg.Connection) -> None:
            conn.execute(f'SET search_path TO "{self.schema}"')
            conn.commit()

        self.pool = ConnectionPool(self.url, min_size=1, max_size=10, configure=configure,
                                   kwargs={"row_factory": dict_row}, open=True)
        return self

    def drop_schema(self) -> None:
        with psycopg.connect(self.url, autocommit=True) as conn:
            conn.execute(f'DROP SCHEMA IF EXISTS "{self.schema}" CASCADE')

    def close(self) -> None:
        if self.pool is not None:
            self.pool.close()

    @contextmanager
    def connection(self):
        assert self.pool is not None, "Database.init() wurde nicht aufgerufen"
        with self.pool.connection() as conn:
            yield conn
