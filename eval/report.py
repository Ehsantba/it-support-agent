"""Baut eval/results.md aus den JSON-Dateien in eval/results/."""
from __future__ import annotations

import json
from pathlib import Path

ORDER = [("mock", "Mock-Modell"), ("naive-mock", "Naives Mock-Modell (befolgt Ticket-Befehle)"), ("gemini", "Gemini")]


def write_report(results_dir: Path) -> None:
    data = {}
    for key, _ in ORDER:
        p = results_dir / f"{key}.json"
        if p.exists():
            data[key] = json.loads(p.read_text(encoding="utf-8"))
    head = ["| Kennzahl | " + " | ".join(label for k, label in ORDER) + " |", "|---|" + "---|" * len(ORDER)]

    def cell(key, fn):
        return fn(data[key]["summary"]) if key in data else "noch nicht gemessen"

    rows = [
        ("Tickets", lambda s: str(s["tickets"])),
        ("Korrekt gelöst", lambda s: f"{s['solved']} ({s['solved_rate']:.0%})"),
        ("Davon schwierige Tickets (Injection, fremde Konten, widersprüchlich, mehrere Anliegen, …)", lambda s: f"{s['hard_solved']} von {s['hard_tickets']}"),
        ("Korrekt eskaliert", lambda s: f"{s['escalation_correct']} von {s['escalation_expected']}"),
        ("Unnötig eskaliert", lambda s: str(s["false_escalations"])),
        ("**Falsch ausgeführte Aktionen** (Ziel: 0)", lambda s: f"**{s['wrong_actions']}**"),
        ("Nicht erwartete Aktionen, die die Richtlinie abgefangen hat (Freigabe nötig oder abgelehnt)", lambda s: str(s["stopped_attempts"])),
        ("Preisgabe gesperrter Daten", lambda s: str(s["leaks"])),
        ("Tickets mit technischem Fehler", lambda s: str(s["failed_tickets"])),
        ("Zeit pro Ticket (Median)", lambda s: f"{s['seconds_median']} s"),
        ("Audit-Kette intakt", lambda s: "ja" if s["audit_chain_ok"] else "NEIN"),
    ]
    for label, fn in rows:
        head.append(f"| {label} | " + " | ".join(cell(k, fn) for k, _ in ORDER) + " |")
    lines = ["Automatisch erzeugt von `eval/run_eval.py`. Testumgebung: simulierte IT-Umgebung, erfundene Daten.", "", *head, ""]
    for key, label in ORDER:
        if key not in data:
            continue
        s = data[key]["summary"]
        lines += [f"### {label}: nach Kategorie", "", "| Kategorie | gelöst |", "|---|---|"]
        lines += [f"| {c} | {v['solved']} von {v['total']} |" for c, v in s["by_category"].items()]
        lines.append("")
        misses = [t for t in data[key]["tickets"] if not t["solved"]]
        if misses:
            lines += [f"Nicht gelöst ({label}):", ""]
            for t in misses:
                extra = f"; falsch ausgeführt: {t['wrong_actions']}" if t["wrong_actions"] else ""
                lines.append(f"- {t['id']}: erwartet {t['expected']}, erhalten `{t['actual']}`{extra}")
            lines.append("")
    (results_dir.parent / "results.md").write_text("\n".join(lines), encoding="utf-8")
