"""Echte Umgebung: Active Directory und Exchange per PowerShell, Entra ID und Intune per Graph."""
from __future__ import annotations

import os

from .base import DirectoryError
from .graph import GraphClient, GraphDirectory
from .powershell import PowerShellDirectory


class CompositeDirectory:
    name = "graph_powershell"

    def __init__(self, graph: GraphDirectory, ps: PowerShellDirectory):
        self.graph, self.ps = graph, ps

    # Active Directory / Exchange
    def get_account_status(self, upn): return self.ps.get_account_status(upn)
    def unlock_account(self, upn): return self.ps.unlock_account(upn)
    def lock_account(self, upn): return self.ps.lock_account(upn)
    def get_mailbox_info(self, upn): return self.ps.get_mailbox_info(upn)
    def set_mailbox_forwarding(self, upn, address): return self.ps.set_mailbox_forwarding(upn, address)
    # Entra ID / Intune
    def get_group_memberships(self, upn): return self.graph.get_group_memberships(upn)
    def get_group_info(self, group): return self.graph.get_group_info(group)
    def get_device_status(self, upn): return self.graph.get_device_status(upn)
    def get_device_owner(self, device_id): return self.graph.get_device_owner(device_id)
    def is_privileged(self, upn): return self.graph.is_privileged(upn)
    def disable_account(self, upn): return self.graph.disable_account(upn)
    def enable_account(self, upn): return self.graph.enable_account(upn)
    def add_group_member(self, upn, group): return self.graph.add_group_member(upn, group)
    def remove_group_member(self, upn, group): return self.graph.remove_group_member(upn, group)
    def trigger_device_sync(self, device_id): return self.graph.trigger_device_sync(device_id)
    def wipe_device(self, device_id): return self.graph.wipe_device(device_id)

    def send_password_reset_link(self, upn):
        raise DirectoryError("Passwort-Reset-Link ist in der echten Umgebung nicht angebunden.")


def build_real_directory() -> CompositeDirectory:
    need = ["GRAPH_TENANT_ID", "GRAPH_CLIENT_ID", "GRAPH_CLIENT_SECRET"]
    missing = [k for k in need if not os.environ.get(k)]
    if missing:
        raise RuntimeError("Fehlende Umgebungsvariablen für die echte Umgebung: " + ", ".join(missing))
    graph = GraphDirectory(GraphClient(os.environ["GRAPH_TENANT_ID"], os.environ["GRAPH_CLIENT_ID"], os.environ["GRAPH_CLIENT_SECRET"]),
                           tuple(x.strip() for x in os.environ.get("PRIVILEGED_GROUPS", "Domain Admins").split(",")))
    return CompositeDirectory(graph, PowerShellDirectory())
