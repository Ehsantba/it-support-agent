"""Audit-Log: nur Anhängen, mit Hash-Kette.

Jeder Eintrag enthält den Hash des vorherigen Eintrags. Wer nachträglich einen Eintrag ändert oder löscht,
bricht die Kette. `verify()` erkennt das. Die Anwendung hat bewusst keine Funktion zum Ändern oder Löschen.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone

from psycopg.types.json import Jsonb

GENESIS = "0" * 64
_LOCK_KEY = 724001


def _canonical(details: dict) -> str:
    return json.dumps(details, sort_keys=True, ensure_ascii=False, separators=(",", ":"), default=str)


def _hash(prev: str, ts_text: str, ticket_id, actor: str, event: str, details: dict) -> str:
    payload = "|".join([prev, ts_text, str(ticket_id), actor, event, _canonical(details)])
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def write(conn, ticket_id: int | None, actor: str, event: str, details: dict | None = None) -> None:
    """Schreibt einen Eintrag in der Transaktion der Verbindung `conn` (Änderung und Protokoll gehören zusammen)."""
    details = json.loads(_canonical(details or {}))          # normalisieren: was gehasht wird, ist was gespeichert wird
    conn.execute("SELECT pg_advisory_xact_lock(%s)", (_LOCK_KEY,))   # Kette serialisieren
    row = conn.execute("SELECT hash FROM audit_log ORDER BY id DESC LIMIT 1").fetchone()
    prev = row["hash"] if row else GENESIS
    now = datetime.now(timezone.utc)
    ts_text = now.isoformat(timespec="microseconds")
    digest = _hash(prev, ts_text, ticket_id, actor, event, details)
    conn.execute(
        "INSERT INTO audit_log (ts, ts_text, ticket_id, actor, event, details, prev_hash, hash) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
        (now, ts_text, ticket_id, actor, event, Jsonb(details), prev, digest))


def verify(conn) -> dict:
    """Prüft die ganze Kette. Gibt {ok, entries, first_bad_id} zurück."""
    prev = GENESIS
    count = 0
    for r in conn.execute("SELECT * FROM audit_log ORDER BY id").fetchall():
        expected = _hash(prev, r["ts_text"], r["ticket_id"], r["actor"], r["event"], r["details"])
        if r["prev_hash"] != prev or r["hash"] != expected:
            return {"ok": False, "entries": count, "first_bad_id": r["id"]}
        prev, count = r["hash"], count + 1
    return {"ok": True, "entries": count, "first_bad_id": None}


def entries(conn, ticket_id: int | None = None, limit: int = 200) -> list[dict]:
    if ticket_id is None:
        rows = conn.execute("SELECT id, ts, ticket_id, actor, event, details FROM audit_log ORDER BY id DESC LIMIT %s", (limit,)).fetchall()
    else:
        rows = conn.execute("SELECT id, ts, ticket_id, actor, event, details FROM audit_log WHERE ticket_id = %s ORDER BY id", (ticket_id,)).fetchall()
    return [{**r, "ts": r["ts"].isoformat()} for r in rows]
