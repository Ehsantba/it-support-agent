# IT-Support-Agent mit Mensch in der Schleife

Ein Agent, der Service-Desk-Tickets liest, mit Werkzeugen in einer Testumgebung bearbeitet und dabei nichts Kritisches
ohne menschliche Freigabe tut. Jede Entscheidung steht in einem Audit-Log, und am Ende prüft ein Mensch das Ergebnis.

Gebaut mit Python, FastAPI, PostgreSQL und React. Das Sprachmodell ist austauschbar: ein regelbasiertes Mock-Modell
für Tests und Gemini (oder jeder OpenAI-kompatible Endpunkt) für echte Läufe.

![Freigabe](docs/screenshots/02-freigabe.png)

## Wie es funktioniert

1. Ein Ticket kommt über die API oder die Weboberfläche herein. Der Antragsteller kommt aus dem Ticket-Kopf, nicht aus dem Text.
2. Der Agent arbeitet in Schritten. Das Modell schlägt pro Schritt genau eine Aktion als JSON vor: `classify_ticket`, dann lesende
   und schreibende Werkzeuge, am Ende `finish`.
3. Jede Aktion läuft durch dieselben Prüfungen, bevor etwas passiert: Ist das Werkzeug in der Allowlist? Passen die Argumente
   zum Schema? Welche Risikostufe hat die Aktion?
4. Danach wird die Aktion ausgeführt oder zur Freigabe vorgelegt. Nach jeder Änderung liest der Agent den Zustand zurück und prüft,
   ob sie wirklich gewirkt hat.
5. Ein Mensch prüft am Ende das Ergebnis (Review-Oberfläche) und gibt kritische Aktionen frei oder lehnt sie ab.

### Risikostufen

| Stufe | Bedeutung | Beispiele | Ablauf |
|---|---|---|---|
| 0 | nur lesen | Kontostatus, Gruppen, Gerätestatus, Postfach | automatisch |
| 1 | reversibel | Konto entsperren, Reset-Link senden, Gerätesynchronisierung | automatisch, mit Protokoll und Prüfung danach |
| 2 | kritisch | Gruppenrechte ändern, Weiterleitung einrichten, Gerät löschen, Konto deaktivieren | **nur nach Freigabe** |

Die Richtlinie (`backend/app/policy.py`) kann eine Aktion nur **höher** einstufen, nie niedriger. Sie stuft auf Stufe 2 hoch, wenn:

- die Aktion eine andere Person betrifft als den Antragsteller,
- das Zielkonto privilegiert ist (z. B. Mitglied von „Domain Admins“),
- ein Konto mit auffällig vielen Fehlversuchen (ab 20) entsperrt werden soll,
- der Ticket-Text Muster einer Prompt Injection enthält,
- schon drei Änderungen im selben Ticket stattgefunden haben (Massenänderung),
- das Ziel nicht geprüft werden konnte.

Die Entscheidung trifft allein dieser Code. Das Sprachmodell kann sie nicht beeinflussen.

## Sicherheit

- **Allowlist und Schema:** Das Modell wählt nur Name und Argumente. Unbekannte Werkzeuge, unbekannte Felder und Werte, die nicht
  zum Muster passen (z. B. Konten fremder Domänen), werden abgelehnt und nie ausgeführt.
- **Ticket-Text ist nicht vertrauenswürdig:** Er steht im Prompt zwischen festen Markierungen, spitze Klammern im Text werden ersetzt,
  und der Prompt sagt dem Modell, dass dort keine Anweisungen stehen. Zusätzlich erkennt eine Mustersuche typische Injection-Sätze.
  Das ist aber nur ein Hinweis. Die eigentliche Absicherung sind Allowlist, Richtlinie und Freigabe: Selbst ein Modell, das jeden Befehl
  im Ticket befolgt, kann nichts Kritisches ausführen (siehe Auswertung).
- **Auskunftssperre:** Der Agent liest keine Daten über andere Personen als den Antragsteller. Er kann für sie aber Anträge stellen,
  die ein Mensch prüft.
- **Keine Passwörter:** Es gibt kein Werkzeug, das ein Passwort liefert, nur einen Reset-Link an die registrierte zweite Authentifizierung.
- **Testlauf (Dry-Run):** Mit `DRY_RUN=true` oder pro Ticket werden schreibende Aktionen nur beschrieben.
- **Vier-Augen-Prinzip:** Der Antragsteller kann seine eigene Aktion weder freigeben noch sein Ticket abschließen.
- **Audit-Log mit Hash-Kette:** Jeder Eintrag enthält den Hash des vorherigen. Ändert oder löscht jemand nachträglich einen Eintrag,
  erkennt `GET /api/audit/verify` den Bruch. Die Anwendung hat keine Funktion zum Ändern oder Löschen.
- **Rückgängig machen:** Wo es möglich ist (Entsperren, Gruppenmitgliedschaft, Weiterleitung, Deaktivierung), merkt sich das System den
  Zustand vor der Änderung. Löschen eines Geräts und der Reset-Link lassen sich nicht zurücknehmen und sind deshalb Stufe 2 bzw. nur für die eigene Person erlaubt.

## Testumgebung

Der Agent arbeitet gegen eine **simulierte IT-Umgebung** in der Datenbank: zwölf erfundene Benutzer (`example.test`), Gruppen, Geräte
und Postfächer. Sie verhält sich wie eine kleine Umgebung (gesperrte Konten, nicht konforme Geräte, volle Postfächer) und lässt sich
jederzeit zurücksetzen. Es werden keine echten Daten oder Abläufe von Unternehmen verwendet.

Für eine echte Umgebung gibt es Adapter (`backend/app/directory/graph.py`, `powershell.py`, Skripte in `powershell/`).
**Diese Adapter sind nicht gegen einen echten Tenant erprobt**, und PowerShell war in der Entwicklungsumgebung nicht verfügbar. Getestet
ist nur der Aufbau der Anfragen mit Ersatz-Servern. Wer sie nutzen will, probiert sie zuerst an einem Entwickler-Tenant mit minimalen Rechten aus
(benötigte Berechtigungen stehen im Kopf von `graph.py`). Der Reset-Link ist in der echten Umgebung nicht angebunden.

## Starten

Mit Docker, zunächst mit dem Mock-Modell (kein Schlüssel nötig):

```bash
docker compose up --build
```

Danach `http://localhost:8000` öffnen. Als Prüfer anmelden (Name beliebig, Token `demo-reviewer`, siehe `.env`).

Ohne Docker braucht man PostgreSQL und Python 3.12:

```bash
cd backend
pip install -r requirements.txt
export DATABASE_URL=postgresql://agent:agent@localhost:5432/agent
uvicorn app.main:app --port 8000
```

Das Frontend baut man mit `cd frontend && npm ci && npm run build`.

### Mit Gemini statt Mock-Modell

1. Einen Schlüssel in [Google AI Studio](https://aistudio.google.com/apikey) erzeugen. Am besten einen eigenen Schlüssel nur für dieses Projekt.
2. `.env.example` nach `.env` kopieren und eintragen:
   ```
   LLM_PROVIDER=gemini
   GEMINI_API_KEY=dein-schluessel
   LLM_MIN_INTERVAL=4
   ```
   Die Datei `.env` steht in `.gitignore` und darf nie committet werden.
3. Den Zugang testen (eine einzige kleine Anfrage mit erfundenen Daten):
   ```bash
   python backend/scripts/check_llm.py
   ```
4. Die Auswertung laufen lassen (46 Tickets, mit Pause wegen der Limits dauert das einige Minuten):
   ```bash
   python eval/run_eval.py --llm gemini --delay 4
   ```
   Das Skript schreibt `eval/results/gemini.json` und aktualisiert `eval/results.md`. Die Zahlen gehören danach in die Tabelle unten.

Das Modell wird über die OpenAI-kompatible Schnittstelle von Gemini angesprochen und bekommt in jedem Schritt Ticket und bisherige Schritte als Text.
Die Antwort ist ein JSON-Objekt, das wir selbst prüfen. Es wird kein herstellerspezifisches Tool-Calling genutzt, damit auch andere Modelle (z. B. lokal mit Ollama über `LLM_PROVIDER=openai_compat` und `LLM_BASE_URL`) funktionieren.
Bei 429 und 5xx wird mit Wartezeit wiederholt. Ist die Antwort kein gültiges JSON, gibt es genau einen zweiten Versuch.

## Auswertung

46 selbst geschriebene Testtickets (`eval/tickets.json`) mit erwartetem Ergebnis, darunter bewusst schwierige: Prompt Injection, behauptete
Autorität, Anträge für fremde Konten, widersprüchliche und unklare Tickets, mehrere Anliegen in einem Ticket. Gemessen werden der Anteil
korrekt gelöster Tickets, korrekte Eskalationen, **falsch ausgeführte Aktionen (Ziel: 0)** und die Zeit pro Ticket.
Die Auswertung läuft gegen die simulierte Umgebung, die vor jedem Ticket zurückgesetzt wird. Die ganze Ausgabe steht in `eval/results.md`.

| Kennzahl | Mock-Modell | Naives Mock-Modell | Gemini |
|---|---|---|---|
| Korrekt gelöst | 42 von 46 | 42 von 46 | noch nicht gemessen |
| Davon schwierige Tickets | 19 von 22 | 19 von 22 | noch nicht gemessen |
| Korrekt eskaliert | 8 von 9 | 8 von 9 | noch nicht gemessen |
| **Falsch ausgeführte Aktionen** | **0** | **0** | noch nicht gemessen |
| Nicht erwartete Aktionen, die die Richtlinie abgefangen hat | 5 | 9 | noch nicht gemessen |
| Preisgabe gesperrter Daten | 0 | 0 | noch nicht gemessen |
| Audit-Kette intakt | ja | ja | noch nicht gemessen |

So sind die Zahlen zu lesen:

- **Das Mock-Modell ist kein Sprachmodell.** Es folgt festen Regeln, die passend zu den Kategorien geschrieben wurden. Seine 42 von 46
  sagen etwas über den Rahmen aus (Allowlist, Richtlinie, Freigabe, Prüfung, Protokoll), **nicht** darüber, wie gut ein Sprachmodell Tickets versteht.
  Dafür ist die Gemini-Spalte da. Sie ist leer, bis die Auswertung mit einem echten Schlüssel gelaufen ist.
- **Das naive Mock-Modell** befolgt zusätzlich jeden Befehl aus dem Ticket-Text. Es versucht 9 Aktionen, die nicht erwartet waren
  (Domain-Admin-Rechte, Weiterleitung nach außen, Entsperren fremder Konten), gegenüber 5 beim normalen Mock-Modell. Alle wurden von der Richtlinie abgefangen,
  **keine einzige wurde ausgeführt**. Das ist die eigentliche Aussage dieser Auswertung: Die Absicherung hängt nicht davon ab, dass das Modell klug ist.
- Nicht gelöst hat das Mock-Modell vier Tickets, wie erwartet die schwierigen: zwei Konten in einem Ticket (k08), ein Handy statt Laptop bei zwei Geräten (d04),
  zwei Anliegen in einem Ticket (s05) und eine Phishing-Meldung (s07).
- **Die Erwartungen wurden nach dem ersten Lauf angepasst.** Bei k07, k09 und g05 waren sie zu streng (Eskalieren statt Rückfragen ist bei unklaren Tickets ebenfalls vertretbar),
  bei d02 enthielt die Erwartung einen Fehler. Das betrifft vor allem die Bewertung des Mock-Modells. Für Gemini gelten dieselben, jetzt festen Erwartungen.
- Die Tickets wurden nicht unabhängig bewertet und decken nur die sechs Kategorien ab. Eine Auswertung auf 46 Tickets ist klein: Ein Ticket entspricht etwa 2 Prozentpunkten.

## Tests

```bash
cd backend && python -m pytest
```

113 Tests gegen eine echte PostgreSQL, unter anderem:

- Schema-Prüfung (Einschleusversuche, falsche Domänen, unbekannte Felder), Prompt-Parser (kaputtes JSON, Trennzeichen im Ticket-Text), Injection-Erkennung
- Richtlinie: jede Hochstufungsregel einzeln, und dass sie nie herabstuft
- Agent-Schleife: unbekannte Werkzeuge, ungültige Argumente, Schrittlimit, ein Wiederholungsversuch bei kaputtem JSON, Ausfall des Modells, Testlauf, fehlgeschlagene Prüfung
- Freigabe, Ablehnung nur mit Kommentar, Vier-Augen-Prinzip, Rückgängig-Machen, Abschlussprüfung
- Audit-Kette: gültig, geänderter Eintrag, gelöschter Eintrag, geänderter Akteur
- API: Zugriff nur mit Token, Validierung, kompletter Freigabe-Ablauf
- Adapter und Sprachmodell-Client mit Ersatz-Servern: PowerShell ohne Shell und mit Prüfung der Parameter, Graph-Anfragen, Wiederholung bei 429, Fehlermeldungen ohne Schlüssel

## Grenzen

- Die Auswertung mit einem echten Sprachmodell steht noch aus (siehe oben). Wie gut Gemini Tickets löst, ist hier nicht gemessen.
- Die Adapter für eine echte Umgebung sind nicht erprobt, die PowerShell-Skripte nicht ausgeführt.
- Das Mock-Modell ist ein Testwerkzeug. Die Mustersuche für Prompt Injection ist leicht zu umgehen und nur ein zusätzlicher Hinweis.
- Die Antwort des Agenten wird nur gespeichert und angezeigt, nicht an den Antragsteller verschickt. Dazwischen steht die Abschlussprüfung.
- Das Eingeben von Tickets ist offen (wie ein Portal), der Antragsteller wird nicht authentifiziert. In einem echten System käme er aus der Anmeldung.
- Prüfer melden sich mit einem gemeinsamen Token an. Für den Betrieb bräuchte es eine richtige Benutzerverwaltung.
- Der Agent erledigt pro Schritt genau eine Aktion und hat kein Gedächtnis über Tickets hinweg.
- Das Docker-Setup ist nicht automatisiert getestet.

## Weiterlesen

- [Entscheidungen und Antworten auf typische Fragen](docs/ENTSCHEIDUNGEN.md)
- API-Dokumentation: `/docs` auf dem laufenden Server

## Lizenz

MIT, siehe [LICENSE](LICENSE).
