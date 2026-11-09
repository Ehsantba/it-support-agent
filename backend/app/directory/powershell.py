"""PowerShell für das lokale Active Directory und Exchange (Kontosperre, Postfach).

STATUS: Gegen eine echte Umgebung nicht erprobt, und PowerShell selbst konnte in der Entwicklungsumgebung nicht ausgeführt werden.
Getestet ist nur der Aufbau des Aufrufs mit einem Ersatz-Runner. Die Skripte liegen in /powershell.

Sicherheit: Es wird kein Befehlstext zusammengebaut. Das Skript wird als eigener Prozess mit Argumentliste gestartet (keine Shell),
Skriptnamen stehen in einer festen Liste, Parameterwerte werden geprüft, und die Skripte validieren sie noch einmal selbst.
"""
from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

from .base import DirectoryError

SCRIPTS_DIR = Path(__file__).resolve().parents[3] / "powershell"
ALLOWED_SCRIPTS = {"Get-AccountStatus.ps1", "Unlock-Account.ps1", "Get-MailboxInfo.ps1", "Set-MailboxForwarding.ps1"}
_PARAM = re.compile(r"^[A-Za-z]{1,30}$")


class PowerShellRunner:
    def __init__(self, executable: str = "pwsh", timeout: float = 30.0, scripts_dir: Path = SCRIPTS_DIR):
        self.exe, self.timeout, self.dir = executable, timeout, scripts_dir

    def build_command(self, script: str, params: dict[str, str]) -> list[str]:
        if script not in ALLOWED_SCRIPTS:
            raise DirectoryError("Skript ist nicht erlaubt")
        cmd = [self.exe, "-NoProfile", "-NonInteractive", "-File", str(self.dir / script)]
        for key, value in params.items():
            if not _PARAM.match(key):
                raise DirectoryError("Ungültiger Parametername")
            if not isinstance(value, str) or value.startswith("-") or "\x00" in value or len(value) > 200:
                raise DirectoryError("Ungültiger Parameterwert")
            cmd += [f"-{key}", value]
        return cmd

    def run(self, script: str, params: dict[str, str]) -> dict:
        cmd = self.build_command(script, params)
        try:
            p = subprocess.run(cmd, capture_output=True, text=True, timeout=self.timeout, shell=False, check=False)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise DirectoryError(f"PowerShell konnte nicht ausgeführt werden ({type(exc).__name__})") from exc
        if p.returncode != 0:
            first = (p.stderr.strip().splitlines() or ["unbekannter Fehler"])[0][:200]
            raise DirectoryError(f"PowerShell-Fehler: {first}")
        try:
            return json.loads(p.stdout)
        except json.JSONDecodeError as exc:
            raise DirectoryError("PowerShell lieferte kein gültiges JSON") from exc


class PowerShellDirectory:
    name = "powershell"

    def __init__(self, runner: PowerShellRunner | None = None):
        self.ps = runner or PowerShellRunner()

    def get_account_status(self, upn):
        return self.ps.run("Get-AccountStatus.ps1", {"Upn": upn})

    def unlock_account(self, upn):
        return self.ps.run("Unlock-Account.ps1", {"Upn": upn})

    def lock_account(self, upn):
        raise DirectoryError("Sperren ist in der echten Umgebung nicht angebunden (Active Directory kennt keinen Sperr-Befehl).")

    def get_mailbox_info(self, upn):
        return self.ps.run("Get-MailboxInfo.ps1", {"Upn": upn})

    def set_mailbox_forwarding(self, upn, address):
        return self.ps.run("Set-MailboxForwarding.ps1", {"Upn": upn, "Address": address or ""} if address else {"Upn": upn, "Clear": "yes"})
