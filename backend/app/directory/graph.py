"""Microsoft Graph (Entra ID, Intune) für Gruppen, Geräte und Konto-Aktivierung.

STATUS: Gegen eine echte Umgebung nicht erprobt. Die Anfragen sind mit einem Ersatz-Server getestet (Aufbau, Fehlerbehandlung),
nicht gegen einen Tenant. Vor dem Einsatz an einem Entwickler-Tenant mit minimalen Rechten ausprobieren.

Benötigte Anwendungsberechtigungen (Least Privilege, je nach genutzten Werkzeugen):
  lesen:      User.Read.All, GroupMember.Read.All, DeviceManagementManagedDevices.Read.All
  Gruppen:    GroupMember.ReadWrite.All
  Konto:      User.EnableDisableAccount.All
  Geräte:     DeviceManagementManagedDevices.PrivilegedOperations.All (Sync, Löschen)
"""
from __future__ import annotations

import re
import time
from urllib.parse import quote

import httpx

from .base import DirectoryError

GRAPH = "https://graph.microsoft.com/v1.0"
_UPN = re.compile(r"^[A-Za-z0-9._%+\-]{1,64}@[A-Za-z0-9.\-]{1,100}$")


class GraphClient:
    def __init__(self, tenant_id: str, client_id: str, client_secret: str, client: httpx.Client | None = None):
        self.tenant, self.cid, self.secret = tenant_id, client_id, client_secret
        self.http = client or httpx.Client(timeout=30)
        self._token, self._exp = None, 0.0

    def _auth(self) -> dict:
        if not self._token or time.time() > self._exp - 60:
            r = self.http.post(f"https://login.microsoftonline.com/{self.tenant}/oauth2/v2.0/token", data={
                "client_id": self.cid, "client_secret": self.secret, "grant_type": "client_credentials",
                "scope": "https://graph.microsoft.com/.default"})
            if r.status_code != 200:
                raise DirectoryError("Anmeldung an Microsoft Graph fehlgeschlagen")
            body = r.json()
            self._token, self._exp = body["access_token"], time.time() + int(body.get("expires_in", 3600))
        return {"Authorization": f"Bearer {self._token}"}

    def request(self, method: str, path: str, **kw) -> dict:
        r = self.http.request(method, GRAPH + path, headers=self._auth(), **kw)
        if r.status_code == 404:
            raise DirectoryError("Objekt nicht gefunden")
        if r.status_code in (401, 403):
            raise DirectoryError("Microsoft Graph verweigert den Zugriff (fehlende Berechtigung)")
        if r.status_code >= 400:
            raise DirectoryError(f"Microsoft Graph Fehler {r.status_code}")
        return r.json() if r.content else {}


def _check_upn(upn: str) -> str:
    if not _UPN.fullmatch(upn):
        raise DirectoryError("Ungültiger Benutzername")
    return quote(upn, safe="@")


class GraphDirectory:
    name = "graph"

    def __init__(self, client: GraphClient, privileged_groups: tuple[str, ...] = ("Domain Admins",)):
        self.g, self.privileged_groups = client, privileged_groups

    def get_account_status(self, upn):
        u = self.g.request("GET", f"/users/{_check_upn(upn)}", params={"$select": "id,displayName,department,accountEnabled,lastPasswordChangeDateTime"})
        return {"upn": upn, "display_name": u.get("displayName"), "department": u.get("department"),
                "enabled": u.get("accountEnabled"), "locked_out": None, "failed_logons": None, "mfa_registered": None,
                "password_last_set": u.get("lastPasswordChangeDateTime")}

    def get_group_memberships(self, upn):
        data = self.g.request("GET", f"/users/{_check_upn(upn)}/memberOf", params={"$select": "displayName"})
        return sorted(x["displayName"] for x in data.get("value", []) if x.get("displayName"))

    def _group_id(self, group: str) -> dict:
        if "'" in group or '"' in group:
            raise DirectoryError("Ungültiger Gruppenname")
        data = self.g.request("GET", "/groups", params={"$filter": f"displayName eq '{group}'", "$select": "id,displayName,description"})
        if not data.get("value"):
            raise DirectoryError(f"Gruppe {group} nicht gefunden")
        return data["value"][0]

    def get_group_info(self, group):
        g = self._group_id(group)
        return {"name": g["displayName"], "description": g.get("description") or "", "privileged": g["displayName"] in self.privileged_groups}

    def is_privileged(self, upn):
        return any(n in self.privileged_groups for n in self.get_group_memberships(upn))

    def get_device_status(self, upn):
        data = self.g.request("GET", "/deviceManagement/managedDevices", params={
            "$filter": f"userPrincipalName eq '{_check_upn(upn)}'", "$select": "id,deviceName,operatingSystem,complianceState,lastSyncDateTime,isEncrypted"})
        return [{"device_id": d["id"], "name": d.get("deviceName"), "os": d.get("operatingSystem"), "compliance": d.get("complianceState"),
                 "last_sync": d.get("lastSyncDateTime"), "encrypted": d.get("isEncrypted"), "wipe_pending": False} for d in data.get("value", [])]

    def get_device_owner(self, device_id):
        d = self.g.request("GET", f"/deviceManagement/managedDevices/{quote(device_id, safe='')}", params={"$select": "userPrincipalName"})
        return d["userPrincipalName"]

    def add_group_member(self, upn, group):
        gid = self._group_id(group)["id"]
        uid = self.g.request("GET", f"/users/{_check_upn(upn)}", params={"$select": "id"})["id"]
        self.g.request("POST", f"/groups/{gid}/members/$ref", json={"@odata.id": f"{GRAPH}/directoryObjects/{uid}"})
        return {"upn": upn, "group": group, "member": True}

    def remove_group_member(self, upn, group):
        gid = self._group_id(group)["id"]
        uid = self.g.request("GET", f"/users/{_check_upn(upn)}", params={"$select": "id"})["id"]
        self.g.request("DELETE", f"/groups/{gid}/members/{uid}/$ref")
        return {"upn": upn, "group": group, "member": False}

    def disable_account(self, upn):
        self.g.request("PATCH", f"/users/{_check_upn(upn)}", json={"accountEnabled": False})
        return {"upn": upn, "enabled": False}

    def enable_account(self, upn):
        self.g.request("PATCH", f"/users/{_check_upn(upn)}", json={"accountEnabled": True})
        return {"upn": upn, "enabled": True}

    def trigger_device_sync(self, device_id):
        self.g.request("POST", f"/deviceManagement/managedDevices/{quote(device_id, safe='')}/syncDevice")
        return {"device_id": device_id, "sync_triggered": True}

    def wipe_device(self, device_id):
        self.g.request("POST", f"/deviceManagement/managedDevices/{quote(device_id, safe='')}/wipe", json={"keepEnrollmentData": False, "keepUserData": False})
        return {"device_id": device_id, "wipe_pending": True}
