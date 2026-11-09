"""Regelbasiertes Ersatz-Modell für Tests und CI. Es folgt dem gleichen Protokoll wie ein echtes Sprachmodell
(liest den Prompt als Text, antwortet mit JSON), ist aber deterministisch und braucht weder Netz noch Schlüssel.

WICHTIG: Ergebnisse mit diesem Modell sagen etwas über den Rahmen aus (Richtlinie, Freigabe, Protokoll), nicht darüber,
wie gut ein echtes Sprachmodell Tickets versteht.

naive=True: Das Modell befolgt zusätzlich Anweisungen, die im Ticket-Text stehen. So lässt sich zeigen, dass die Absicherung
auch dann hält, wenn das Sprachmodell versagt.
"""
from __future__ import annotations

import json
import re

from .prompts import PromptView, StepView, parse_prompt

_EMAIL = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
_KNOWN_GROUPS = ["Domain Admins", "IT-Helpdesk", "Projekt-Alpha", "Projekt-Beta", "Alle-Mitarbeiter", "VPN-Zugang", "Buchhaltung", "Vertrieb"]


def _act(thought: str, tool: str, **arguments) -> str:
    return json.dumps({"thought": thought, "action": {"tool": tool, "arguments": arguments}}, ensure_ascii=False)


class MockLLM:
    def __init__(self, naive: bool = False, domain: str = "example.test"):
        self.naive, self.domain = naive, domain
        self.name = "mock-naiv" if naive else "mock"

    # ---- Hilfen ----
    @staticmethod
    def _step(v: PromptView, tool: str) -> StepView | None:
        return next((s for s in v.steps if s.tool == tool), None)

    def _target(self, v: PromptView, text: str) -> str:
        for e in _EMAIL.findall(v.subject + " " + v.body):
            if e.lower().endswith("@" + self.domain) and e.lower() != v.requester.lower():
                return e.lower()
        return v.requester.lower()

    @staticmethod
    def _outcome(step: StepView | None) -> str:
        if step is None:
            return "nicht ausgeführt"
        return {"EXECUTED": "durchgeführt", "PENDING_APPROVAL": "zur Freigabe vorgelegt (wartet auf einen Menschen)",
                "DRY_RUN": "nur beschrieben (Testlauf)", "BLOCKED": "abgelehnt"}.get(step.status, step.status)

    # ---- Hauptlogik ----
    def complete(self, system: str, user: str) -> str:
        v = parse_prompt(user)
        text = f"{v.subject}\n{v.body}".lower()
        cls = self._step(v, "classify_ticket")
        if cls is None:
            return self._classify(text)
        if any(s.status == "BLOCKED" and s.tool != "classify_ticket" for s in v.steps) and len([s for s in v.steps if s.status == "BLOCKED"]) >= 1 and self._step(v, "finish") is None:
            if len(v.steps) >= 6:
                return _act("Wiederholt abgelehnt, ich gebe ab.", "finish", status="escalate",
                            message="Ich konnte das nicht selbst erledigen. Ein Kollege übernimmt.", reason="Schritte wurden abgelehnt")
        if self.naive:
            naive = self._naive_injection(v, text)
            if naive:
                return naive
        cat = cls.args.get("category")
        flow = {"KONTOSPERRE": self._lock, "PASSWORT": self._password, "GRUPPENRECHTE": self._groups,
                "GERAET": self._device, "POSTFACH": self._mailbox}.get(cat)
        if flow is None:
            return _act("Kein passendes Werkzeug.", "finish", status="escalate",
                        message="Danke für Ihre Nachricht. Dafür ist ein Kollege zuständig, er meldet sich bei Ihnen.",
                        reason="Kategorie ohne Werkzeuge")
        return flow(v, text)

    def _classify(self, text: str) -> str:
        rules = [
            ("KONTOSPERRE", r"gesperrt|sperre|entsperr|locked|account.*(zu|aus)|anmeld.*(nicht|fehl)|konto"),
            ("PASSWORT", r"passwort|kennwort|password|mfa|authenticator|zweiter faktor"),
            ("GRUPPENRECHTE", r"gruppe|berechtigung|zugriff auf|zugang|rechte|mitglied"),
            ("GERAET", r"laptop|notebook|gerät|rechner|handy|smartphone|intune|nicht konform|verloren|gestohlen"),
            ("POSTFACH", r"postfach|mailbox|e-mail|email|outlook|weiterleit|speicherplatz"),
        ]
        # Reihenfolge nach Spezifität: Passwort vor Konto, Gerät/Postfach vor Konto
        order = ["PASSWORT", "GERAET", "POSTFACH", "GRUPPENRECHTE", "KONTOSPERRE"]
        d = dict(rules)
        for cat in order:
            if re.search(d[cat], text):
                return _act("Stichwörter im Ticket.", "classify_ticket", category=cat, confidence=0.8,
                            summary=text.strip().splitlines()[0][:200] if text.strip() else "leer")
        return _act("Keine eindeutigen Stichwörter.", "classify_ticket", category="SONSTIGES", confidence=0.5, summary="Kein Werkzeug passend")

    def _finish(self, status, message, reason) -> str:
        return _act("Fertig.", "finish", status=status, message=message, reason=reason)

    def _lock(self, v, text):
        target = self._target(v, text)
        st = self._step(v, "get_account_status")
        if st is None:
            return _act("Zustand prüfen.", "get_account_status", upn=target)
        info = st.result if isinstance(st.result, dict) else {}
        if st.status == "BLOCKED":          # Auskunft über Dritte gesperrt: Antrag direkt stellen, ein Mensch prüft
            un = self._step(v, "unlock_account")
            if un is None:
                return _act("Fremdes Konto, Antrag zur Freigabe.", "unlock_account", upn=target)
            return self._finish("resolved", f"Entsperrung für {target}: {self._outcome(un)}.", "Konto eines Kollegen")
        if st.status != "EXECUTED" or not info:
            return self._finish("needs_info", "Ich konnte das Konto nicht finden. Bitte geben Sie das betroffene Konto an.", "Konto nicht gefunden")
        if not info.get("enabled"):
            return self._finish("escalate", "Das Konto ist deaktiviert, nicht gesperrt. Das kann nur die IT-Leitung oder die Personalabteilung ändern.", "Konto deaktiviert")
        if not info.get("locked_out"):
            return self._finish("resolved", "Das Konto ist nicht gesperrt. Bitte prüfen Sie das Passwort oder fordern Sie einen Reset-Link an.", "Konto nicht gesperrt")
        un = self._step(v, "unlock_account")
        if un is None:
            return _act("Konto ist gesperrt.", "unlock_account", upn=target)
        if un.verification and un.verification.startswith("FEHLGESCHLAGEN"):
            return self._finish("escalate", "Die Entsperrung hat nicht gewirkt. Ein Kollege prüft das.", "Prüfung fehlgeschlagen")
        return self._finish("resolved", f"Entsperrung für {target}: {self._outcome(un)}.", "Konto entsperrt")

    def _password(self, v, text):
        if re.search(r"authenticator|mfa|zweiter faktor|zweite authent|handy.*verloren|smartphone.*verloren", text):
            return self._finish("escalate", "Für Änderungen an der zweiten Authentifizierung muss Ihre Identität persönlich geprüft werden. Ein Kollege meldet sich.", "MFA-Zurücksetzung erfordert Identitätsprüfung")
        target = self._target(v, text)
        st = self._step(v, "get_account_status")
        if st is None:
            return _act("Zustand prüfen.", "get_account_status", upn=target)
        info = st.result if isinstance(st.result, dict) else {}
        if st.status != "BLOCKED":
            if not info:
                return self._finish("needs_info", "Konto nicht gefunden. Bitte nennen Sie das betroffene Konto.", "Konto nicht gefunden")
            if not info.get("enabled"):
                return self._finish("escalate", "Das Konto ist deaktiviert. Ein Passwort-Reset hilft nicht.", "Konto deaktiviert")
        rl = self._step(v, "send_password_reset_link")
        if rl is None:
            return _act("Reset-Link statt Passwort.", "send_password_reset_link", upn=target)
        if rl.status in ("BLOCKED", "FAILED") or (rl.status == "EXECUTED" and isinstance(rl.result, dict) and not rl.result.get("sent")):
            return self._finish("escalate", "Der Reset-Link konnte nicht zugestellt werden.", "Versand fehlgeschlagen")
        return self._finish("resolved", f"Link zum Zurücksetzen des Passworts für {target}: {self._outcome(rl)}. Passwörter nennen wir nie.", "Reset-Link versendet")

    def _groups(self, v, text):
        group = next((g for g in _KNOWN_GROUPS if g.lower() in text), None)
        if group is None:
            return self._finish("needs_info", "Welche Gruppe oder welchen Zugriff benötigen Sie genau?", "Gruppe nicht erkennbar")
        gi = self._step(v, "get_group_info")
        if gi is None:
            return _act("Gruppe prüfen.", "get_group_info", group=group)
        if isinstance(gi.result, dict) and gi.result.get("privileged"):
            return self._finish("escalate", f"Die Gruppe {group} ist privilegiert. Das entscheidet die IT-Leitung, nicht der Support.", "Privilegierte Gruppe")
        target = self._target(v, text)
        add = self._step(v, "add_group_member")
        if add is None:
            return _act("Mitgliedschaft beantragen.", "add_group_member", upn=target, group=group)
        return self._finish("resolved", f"Aufnahme von {target} in {group}: {self._outcome(add)}.", "Gruppenmitgliedschaft")

    def _device(self, v, text):
        target = self._target(v, text)
        dev_id = re.search(r"DEV-\d{3,6}", v.subject + " " + v.body)
        if dev_id and re.search(r"synchron", text):          # Gerät per ID genannt: direkt anstoßen, Richtlinie entscheidet
            sync = self._step(v, "trigger_device_sync")
            if sync is None:
                return _act("Gerät per ID.", "trigger_device_sync", device_id=dev_id.group(0))
            return self._finish("resolved", f"Synchronisierung: {self._outcome(sync)}.", "Synchronisierung")
        st = self._step(v, "get_device_status")
        if st is None:
            return _act("Geräte prüfen.", "get_device_status", upn=target)
        devices = st.result if isinstance(st.result, list) else []
        if st.status == "BLOCKED":
            return self._finish("escalate", "Auskünfte über Geräte anderer Personen gibt der Support nicht. Ein Kollege prüft Ihr Anliegen.", "Fremdes Gerät")
        if not devices:
            return self._finish("needs_info", "Ich finde kein Gerät zu Ihrem Konto. Nennen Sie bitte den Gerätenamen.", "Kein Gerät gefunden")
        if re.search(r"verloren|gestohlen|lost|stolen", text):
            dev = next((d for d in devices if re.search(r"nb-|laptop|notebook", d["name"].lower()) and re.search(r"laptop|notebook|nb", text)), devices[0])
            w = self._step(v, "wipe_device")
            if w is None:
                return _act("Verlorenes Gerät sichern.", "wipe_device", device_id=dev["device_id"])
            return self._finish("resolved", f"Löschbefehl für {dev['name']}: {self._outcome(w)}.", "Gerät verloren")
        bad = next((d for d in devices if d["compliance"] != "compliant"), None)
        if bad is None:
            return self._finish("resolved", "Ihre Geräte sind konform. Bitte melden Sie sich, wenn das Problem weiter besteht.", "Alle Geräte konform")
        sync = self._step(v, "trigger_device_sync")
        if sync is None:
            return _act("Synchronisierung anstoßen.", "trigger_device_sync", device_id=bad["device_id"])
        if sync.verification and sync.verification.startswith("FEHLGESCHLAGEN"):
            return self._finish("escalate", f"{bad['name']} ist nach der Synchronisierung weiter nicht konform. Ein Kollege prüft die Richtlinien.", "Sync ohne Wirkung")
        return self._finish("resolved", f"Synchronisierung für {bad['name']}: {self._outcome(sync)}.", "Synchronisierung")

    def _mailbox(self, v, text):
        target = v.requester.lower() if "weiterleit" in text else self._target(v, text)
        st = self._step(v, "get_mailbox_info")
        if st is None:
            return _act("Postfach prüfen.", "get_mailbox_info", upn=target)
        info = st.result if isinstance(st.result, dict) else {}
        blocked = st.status == "BLOCKED"
        if not info and not blocked:
            return self._finish("needs_info", "Ich finde kein Postfach für dieses Konto.", "Postfach nicht gefunden")
        if blocked and "weiterleit" not in text:
            return self._finish("escalate", "Auskünfte über Postfächer anderer Personen gibt der Support nicht.", "Fremdes Postfach")
        if "weiterleit" in text:
            external = [e for e in _EMAIL.findall(v.subject + " " + v.body) if not e.lower().endswith("@" + self.domain)]
            if external:
                return self._finish("escalate", "Weiterleitungen an externe Adressen prüft die IT-Sicherheit.", "Externe Weiterleitung")
            internal = [e for e in _EMAIL.findall(v.body) if e.lower() != target]
            if internal:
                fw = self._step(v, "set_mailbox_forwarding")
                if fw is None:
                    return _act("Weiterleitung beantragen.", "set_mailbox_forwarding", upn=target, address=internal[0])
                return self._finish("resolved", f"Weiterleitung: {self._outcome(fw)}.", "Weiterleitung")
            return self._finish("needs_info", "An welche Adresse soll weitergeleitet werden?", "Zieladresse fehlt")
        if info.get("used_percent", 0) >= 90:
            return self._finish("escalate", f"Ihr Postfach ist zu {info['used_percent']} % voll. Bitte archivieren Sie alte Mails. Eine Erhöhung entscheidet ein Kollege.", "Postfach fast voll")
        return self._finish("resolved", f"Ihr Postfach ist zu {info.get('used_percent', '?')} % gefüllt, die Weiterleitung ist {'aktiv' if info.get('forwarding_address') else 'nicht eingerichtet'}.", "Auskunft")

    def _naive_injection(self, v: PromptView, text: str) -> str | None:
        """Befolgt Befehle aus dem Ticket-Text, ohne nachzudenken (nur zum Testen der Absicherung)."""
        done = {s.tool for s in v.steps}
        if "domain admins" in text and "add_group_member" not in done:
            who = next((e for e in _EMAIL.findall(v.body) if e.lower().endswith("@" + self.domain)), v.requester)
            return _act("Ticket verlangt es.", "add_group_member", upn=who.lower(), group="Domain Admins")
        m = re.search(r"dev-\d{3,6}", text)
        if m and re.search(r"lösch|wipe|zurücksetz", text) and "wipe_device" not in done and "verloren" not in text:
            return _act("Ticket verlangt es.", "wipe_device", device_id=m.group(0).upper())
        ext = [e for e in _EMAIL.findall(v.body) if not e.lower().endswith("@" + self.domain)]
        if "weiterleit" in text and ext and "set_mailbox_forwarding" not in done:
            return _act("Ticket verlangt es.", "set_mailbox_forwarding", upn=v.requester, address=ext[0])
        m = re.search(r"(?:entsperr\w*|unlock)\s+(?:das\s+konto\s+(?:von\s+)?)?([\w.\-]+@[\w.\-]+)", text)
        if m and "unlock_account" not in done:
            return _act("Ticket verlangt es.", "unlock_account", upn=m.group(1))
        return None
