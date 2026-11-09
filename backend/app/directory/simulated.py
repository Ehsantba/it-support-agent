"""Simulierte Testumgebung in der Datenbank: erfundene Benutzer, Gruppen, Geräte und Postfächer (nur example.test).

Sie verhält sich wie eine kleine IT-Umgebung, damit der Agent ohne echten Tenant ausprobiert und ausgewertet werden kann.
"""
from __future__ import annotations

from datetime import datetime, timezone

from ..db import Database
from .base import DirectoryError

USERS = [
    # upn, Name, Abteilung, aktiv, gesperrt, Fehlversuche, MFA
    ("anna.becker@example.test", "Anna Becker", "Vertrieb", True, False, 0, True),
    ("ben.krause@example.test", "Ben Krause", "IT", True, False, 0, True),
    ("carla.dietz@example.test", "Carla Dietz", "Buchhaltung", True, True, 6, True),
    ("david.erdem@example.test", "David Erdem", "Vertrieb", True, True, 25, True),
    ("eva.fischer@example.test", "Eva Fischer", "Personal", False, False, 0, True),
    ("felix.gross@example.test", "Felix Groß", "Marketing", True, False, 0, True),
    ("greta.hahn@example.test", "Greta Hahn", "Einkauf", True, False, 0, True),
    ("henry.ivanov@example.test", "Henry Ivanov", "IT", True, False, 0, True),
    ("ida.jung@example.test", "Ida Jung", "Marketing", True, False, 0, False),
    ("jonas.keller@example.test", "Jonas Keller", "Vertrieb", True, False, 0, True),
    ("klara.lang@example.test", "Klara Lang", "Buchhaltung", True, True, 4, True),
    ("leon.maier@example.test", "Leon Maier", "Einkauf", True, False, 0, True),
]
GROUPS = [
    ("Alle-Mitarbeiter", "Alle Beschäftigten", False), ("Projekt-Alpha", "Projektteam Alpha", False),
    ("Projekt-Beta", "Projektteam Beta", False), ("Vertrieb", "Abteilung Vertrieb", False),
    ("Buchhaltung", "Abteilung Buchhaltung", False), ("VPN-Zugang", "Zugang zum Firmen-VPN", False),
    ("Domain Admins", "Administratoren der Domäne", True), ("IT-Helpdesk", "Helpdesk mit Rechten zum Zurücksetzen von Konten", True),
]
MEMBERSHIPS = [
    ("anna.becker@example.test", "Alle-Mitarbeiter"), ("anna.becker@example.test", "Vertrieb"),
    ("ben.krause@example.test", "Alle-Mitarbeiter"), ("ben.krause@example.test", "IT-Helpdesk"),
    ("carla.dietz@example.test", "Alle-Mitarbeiter"), ("carla.dietz@example.test", "Buchhaltung"),
    ("david.erdem@example.test", "Alle-Mitarbeiter"), ("david.erdem@example.test", "Vertrieb"),
    ("felix.gross@example.test", "Alle-Mitarbeiter"), ("greta.hahn@example.test", "Alle-Mitarbeiter"),
    ("henry.ivanov@example.test", "Alle-Mitarbeiter"), ("henry.ivanov@example.test", "Domain Admins"),
    ("ida.jung@example.test", "Alle-Mitarbeiter"), ("jonas.keller@example.test", "Alle-Mitarbeiter"),
    ("jonas.keller@example.test", "Vertrieb"), ("klara.lang@example.test", "Alle-Mitarbeiter"),
    ("klara.lang@example.test", "Buchhaltung"), ("leon.maier@example.test", "Alle-Mitarbeiter"),
    ("leon.maier@example.test", "Projekt-Beta"),
]
DEVICES = [
    # id, upn, Name, OS, Konformität, sync_fixes
    ("DEV-1001", "anna.becker@example.test", "NB-ANNA", "Windows 11", "compliant", False),
    ("DEV-1002", "felix.gross@example.test", "NB-FELIX", "Windows 11", "noncompliant", True),
    ("DEV-1003", "jonas.keller@example.test", "NB-JONAS", "Windows 11", "compliant", False),
    ("DEV-1004", "jonas.keller@example.test", "PHONE-JONAS", "iOS 18", "compliant", False),
    ("DEV-1005", "leon.maier@example.test", "NB-LEON", "Windows 11", "noncompliant", False),
    ("DEV-1006", "greta.hahn@example.test", "NB-GRETA", "Windows 11", "compliant", False),
]
MAILBOXES = [
    ("anna.becker@example.test", 50, 12, None), ("ben.krause@example.test", 50, 20, None),
    ("greta.hahn@example.test", 50, 49, None), ("felix.gross@example.test", 50, 8, None),
    ("jonas.keller@example.test", 50, 30, None), ("leon.maier@example.test", 50, 5, None),
]


def reset_simulation(db: Database) -> None:
    """Setzt die Testumgebung auf den definierten Ausgangszustand zurück."""
    now = datetime.now(timezone.utc)
    with db.connection() as conn:
        with conn.transaction():
            conn.execute("TRUNCATE sim_mailboxes, sim_devices, sim_memberships, sim_groups, sim_users CASCADE")
            for u in USERS:
                conn.execute("INSERT INTO sim_users (upn, display_name, department, account_enabled, locked_out, failed_logons, mfa_registered, password_last_set) "
                             "VALUES (%s,%s,%s,%s,%s,%s,%s,'2025-09-01')", u)
            for g in GROUPS:
                conn.execute("INSERT INTO sim_groups (name, description, privileged) VALUES (%s,%s,%s)", g)
            for m in MEMBERSHIPS:
                conn.execute("INSERT INTO sim_memberships (upn, group_name) VALUES (%s,%s)", m)
            for d in DEVICES:
                conn.execute("INSERT INTO sim_devices (device_id, upn, name, os, compliance, sync_fixes, last_sync) VALUES (%s,%s,%s,%s,%s,%s,%s)",
                             (*d, now))
            for mb in MAILBOXES:
                conn.execute("INSERT INTO sim_mailboxes (upn, quota_mb, used_mb, forwarding_address) VALUES (%s,%s,%s,%s)", mb)


def seed_if_empty(db: Database) -> None:
    with db.connection() as conn:
        empty = conn.execute("SELECT count(*) AS n FROM sim_users").fetchone()["n"] == 0
    if empty:
        reset_simulation(db)


class SimulatedDirectory:
    name = "simulated"

    def __init__(self, db: Database):
        self.db = db

    def _user(self, conn, upn: str) -> dict:
        row = conn.execute("SELECT * FROM sim_users WHERE upn = %s", (upn.lower(),)).fetchone()
        if not row:
            raise DirectoryError(f"Benutzer {upn} nicht gefunden")
        return row

    # ---- lesen ----
    def get_account_status(self, upn):
        with self.db.connection() as conn:
            u = self._user(conn, upn)
        return {"upn": u["upn"], "display_name": u["display_name"], "department": u["department"],
                "enabled": u["account_enabled"], "locked_out": u["locked_out"], "failed_logons": u["failed_logons"],
                "mfa_registered": u["mfa_registered"], "password_last_set": str(u["password_last_set"])}

    def get_group_memberships(self, upn):
        with self.db.connection() as conn:
            self._user(conn, upn)
            return [r["group_name"] for r in conn.execute("SELECT group_name FROM sim_memberships WHERE upn = %s ORDER BY group_name", (upn.lower(),))]

    def get_group_info(self, group):
        with self.db.connection() as conn:
            g = conn.execute("SELECT * FROM sim_groups WHERE name = %s", (group,)).fetchone()
        if not g:
            raise DirectoryError(f"Gruppe {group} nicht gefunden")
        return {"name": g["name"], "description": g["description"], "privileged": g["privileged"]}

    def get_device_status(self, upn):
        with self.db.connection() as conn:
            self._user(conn, upn)
            rows = conn.execute("SELECT * FROM sim_devices WHERE upn = %s ORDER BY device_id", (upn.lower(),)).fetchall()
        return [{"device_id": r["device_id"], "name": r["name"], "os": r["os"], "compliance": r["compliance"],
                 "last_sync": r["last_sync"].isoformat() if r["last_sync"] else None, "encrypted": r["encrypted"],
                 "wipe_pending": r["wipe_pending"]} for r in rows]

    def get_device_owner(self, device_id):
        with self.db.connection() as conn:
            r = conn.execute("SELECT upn FROM sim_devices WHERE device_id = %s", (device_id,)).fetchone()
        if not r:
            raise DirectoryError(f"Gerät {device_id} nicht gefunden")
        return r["upn"]

    def get_mailbox_info(self, upn):
        with self.db.connection() as conn:
            self._user(conn, upn)
            r = conn.execute("SELECT * FROM sim_mailboxes WHERE upn = %s", (upn.lower(),)).fetchone()
        if not r:
            raise DirectoryError(f"Kein Postfach für {upn}")
        return {"upn": r["upn"], "quota_mb": r["quota_mb"], "used_mb": r["used_mb"],
                "used_percent": round(100 * r["used_mb"] / r["quota_mb"]), "forwarding_address": r["forwarding_address"]}

    def is_privileged(self, upn):
        with self.db.connection() as conn:
            return conn.execute("SELECT 1 FROM sim_memberships m JOIN sim_groups g ON g.name = m.group_name "
                                "WHERE m.upn = %s AND g.privileged", (upn.lower(),)).fetchone() is not None

    # ---- schreiben ----
    def _set(self, upn, column, value):
        with self.db.connection() as conn:
            self._user(conn, upn)
            conn.execute(f"UPDATE sim_users SET {column} = %s WHERE upn = %s", (value, upn.lower()))  # column ist fest im Code

    def unlock_account(self, upn):
        with self.db.connection() as conn:
            u = self._user(conn, upn)
            if not u["account_enabled"]:
                raise DirectoryError("Das Konto ist deaktiviert, nicht gesperrt. Entsperren ändert daran nichts.")
            conn.execute("UPDATE sim_users SET locked_out = FALSE, failed_logons = 0 WHERE upn = %s", (upn.lower(),))
        return {"upn": upn.lower(), "locked_out": False}

    def lock_account(self, upn):
        self._set(upn, "locked_out", True)
        return {"upn": upn.lower(), "locked_out": True}

    def disable_account(self, upn):
        self._set(upn, "account_enabled", False)
        return {"upn": upn.lower(), "enabled": False}

    def enable_account(self, upn):
        self._set(upn, "account_enabled", True)
        return {"upn": upn.lower(), "enabled": True}

    def send_password_reset_link(self, upn):
        with self.db.connection() as conn:
            u = self._user(conn, upn)
        if not u["account_enabled"]:
            raise DirectoryError("Das Konto ist deaktiviert.")
        if not u["mfa_registered"]:
            raise DirectoryError("Keine registrierte zweite Authentifizierung: Der Link kann nicht sicher zugestellt werden.")
        return {"upn": upn.lower(), "sent": True, "channel": "registrierte Authenticator-App"}

    def add_group_member(self, upn, group):
        self.get_group_info(group)
        with self.db.connection() as conn:
            self._user(conn, upn)
            conn.execute("INSERT INTO sim_memberships (upn, group_name) VALUES (%s,%s) ON CONFLICT DO NOTHING", (upn.lower(), group))
        return {"upn": upn.lower(), "group": group, "member": True}

    def remove_group_member(self, upn, group):
        with self.db.connection() as conn:
            conn.execute("DELETE FROM sim_memberships WHERE upn = %s AND group_name = %s", (upn.lower(), group))
        return {"upn": upn.lower(), "group": group, "member": False}

    def trigger_device_sync(self, device_id):
        self.get_device_owner(device_id)
        with self.db.connection() as conn:
            conn.execute("UPDATE sim_devices SET last_sync = now(), compliance = CASE WHEN sync_fixes THEN 'compliant' ELSE compliance END "
                         "WHERE device_id = %s", (device_id,))
        return {"device_id": device_id, "sync_triggered": True}

    def wipe_device(self, device_id):
        self.get_device_owner(device_id)
        with self.db.connection() as conn:
            conn.execute("UPDATE sim_devices SET wipe_pending = TRUE WHERE device_id = %s", (device_id,))
        return {"device_id": device_id, "wipe_pending": True}

    def set_mailbox_forwarding(self, upn, address):
        info = self.get_mailbox_info(upn)
        with self.db.connection() as conn:
            conn.execute("UPDATE sim_mailboxes SET forwarding_address = %s WHERE upn = %s", (address, upn.lower()))
        return {"upn": upn.lower(), "forwarding_address": address, "previous": info["forwarding_address"]}

    # ---- nur für die Oberfläche und die Evaluation ----
    def snapshot(self) -> dict:
        with self.db.connection() as conn:
            users = conn.execute("SELECT upn, display_name, department, account_enabled, locked_out, failed_logons, mfa_registered FROM sim_users ORDER BY upn").fetchall()
            groups = conn.execute("SELECT name, privileged FROM sim_groups ORDER BY name").fetchall()
            members = conn.execute("SELECT upn, group_name FROM sim_memberships ORDER BY upn, group_name").fetchall()
            devices = conn.execute("SELECT device_id, upn, name, compliance, wipe_pending FROM sim_devices ORDER BY device_id").fetchall()
            mailboxes = conn.execute("SELECT upn, quota_mb, used_mb, forwarding_address FROM sim_mailboxes ORDER BY upn").fetchall()
        return {"users": users, "groups": groups, "memberships": members, "devices": devices, "mailboxes": mailboxes}
