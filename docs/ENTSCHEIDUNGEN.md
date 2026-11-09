# Entscheidungen und Antworten auf typische Fragen

Zahlen stammen aus `eval/results.md` (simulierte Umgebung, 46 Testtickets). Die Auswertung mit einem echten Sprachmodell steht noch aus.

## Warum diese Risikostufen, und wer entscheidet, was kritisch ist?

Die Frage ist: Was passiert, wenn die Aktion falsch war?

- **Stufe 0 (lesen):** nichts. Deshalb automatisch.
- **Stufe 1 (reversibel):** Der Schaden ist klein und lässt sich zurücknehmen (Konto wieder sperren, Sync wiederholen). Automatisch, aber mit Zustandsaufnahme vorher, Prüfung nachher und Protokoll.
- **Stufe 2 (kritisch):** Der Schaden ist groß oder nicht rückgängig zu machen: Rechte vergeben (Rechteausweitung), Daten nach außen leiten (Datenabfluss), Daten löschen. Nur nach Freigabe.

Wer entscheidet? Nicht das Sprachmodell, sondern die Richtlinie in `policy.py`, also Code, den Menschen schreiben und prüfen. Die Basisstufe jedes Werkzeugs steht in `tools.py`.
Die Richtlinie stuft nur hoch, nie herunter. Das ist Absicht: Eine Regel, die etwas herabstufen könnte, wäre ein Angriffspunkt.
Hochgestuft wird nach Kontext: andere Person als der Antragsteller, privilegiertes Zielkonto, auffällig viele Fehlversuche, Injection-Verdacht, Massenänderung.
Wer ein Werkzeug als „kritisch“ einstuft, ist eine Entscheidung der IT-Verantwortlichen im Betrieb. Hier ist sie als Beispiel festgelegt und im Code nachvollziehbar.

## Wie schützt man sich vor Prompt Injection im Ticket-Text?

Ehrlich gesagt: Man kann nicht verhindern, dass ein Sprachmodell manipuliert wird. Deshalb ist das Ziel, dass Manipulation **keinen Schaden anrichten kann**.

1. **Berechtigung liegt nicht beim Modell.** Das Modell schlägt Aktionen nur vor. Allowlist, Schema und Richtlinie laufen danach in normalem Code.
2. **Identität kommt aus den Metadaten.** Die Richtlinie vergleicht das Ziel der Aktion mit dem Antragsteller aus dem Ticket-Kopf, nicht mit dem, was im Text behauptet wird („Ich bin der IT-Leiter“).
3. **Kritisches braucht einen Menschen.** Auch wenn das Modell komplett kapert wird, landet eine Rechteänderung nur als Freigabe-Antrag in der Warteschlange.
4. **Verdacht verschärft alles.** Erkennt die Mustersuche Injection-Sätze, werden alle Änderungen im Ticket auf Stufe 2 gesetzt. Die Mustersuche ist leicht zu umgehen und deshalb nur ein Zusatz, keine Verteidigung.
5. **Der Ticket-Text kann den Prompt nicht verlassen.** Er steht zwischen Markierungen, `<` und `>` werden ersetzt, und Schritte zählen nur hinter dem Ticket-Block.
6. **Keine Daten über Dritte.** Selbst ein manipuliertes Modell kann Informationen über andere Personen nicht lesen (Auskunftssperre).

Nachgewiesen wird das mit dem **naiven Mock-Modell**, das jeden Befehl im Ticket befolgt: Es versucht neun nicht erwartete Aktionen, alle werden abgefangen, keine ausgeführt (`eval/results.md`).

## Was passiert, wenn das Modell ein falsches Werkzeug wählt?

- **Gibt es das Werkzeug nicht:** Der Schritt wird mit Status `BLOCKED` gespeichert, nicht ausgeführt, und das Modell bekommt die Ablehnung im Verlauf zu sehen. Nach drei ungültigen Schritten wird das Ticket eskaliert.
- **Falsche Argumente:** Das Schema lehnt sie ab (fremde Domäne, unbekannte Felder, Einschleusversuche).
- **Ein existierendes, aber unpassendes Werkzeug:** Die Richtlinie prüft nicht, ob die Aktion *sinnvoll* ist, sondern ob sie *riskant* ist. Ein falsches Stufe-1-Werkzeug auf das eigene Konto läuft durch, ist aber reversibel und wird nach der Ausführung geprüft und protokolliert. Ein falsches Stufe-2-Werkzeug braucht Freigabe, und der Mensch sieht Ziel und Begründung.
- **Das Ergebnis stimmt nicht:** Nach jeder Änderung liest das System den Zustand zurück. Schlägt die Prüfung fehl (z. B. Gerät bleibt nach dem Sync nicht konform), steht das beim Schritt, und der Agent eskaliert.
- **Das Modell antwortet kein gültiges JSON:** Ein zweiter Versuch mit Hinweis, danach Status `FAILED` und der Fehler im Ticket.

Was das nicht verhindert: dass das Modell ein *harmloses*, aber *falsches* Stufe-1-Werkzeug wählt, etwa das falsche Gerät synchronisiert. Das fängt erst die Abschlussprüfung durch den Menschen.

## Wie werden Aktionen nachvollziehbar und rückgängig?

**Nachvollziehbar:** Jeder Schritt hat einen Eintrag in `actions` (Werkzeug, Argumente, Stufe, Begründung der Richtlinie, Status, Ergebnis, Prüfung) und mehrere Einträge im Audit-Log
(Entscheidung des Modells, Risikobewertung, Ausführung, Freigabe, Urteil). Änderung und Protokolleintrag stehen in derselben Datenbank-Transaktion.
Das Audit-Log ist eine Hash-Kette: Wer einen Eintrag ändert oder löscht, bricht sie, und `verify()` meldet den ersten betroffenen Eintrag.
Ehrlich eingeordnet: Das erkennt Manipulation, verhindert sie aber nicht. Wer Zugriff auf die Datenbank hat, könnte die ganze Kette neu berechnen. Für echten Schutz müsste der letzte Hash regelmäßig an einen Ort außerhalb gespeichert werden.

**Rückgängig:** Vor jeder Änderung hält `capture` den Zustand fest (war das Konto vorher gesperrt, war die Person schon Mitglied, wie lautete die alte Weiterleitung).
`undo` stellt ihn wieder her, nur für Prüfer, und wird ebenfalls protokolliert. Nicht rückgängig zu machen sind das Löschen eines Geräts und der Reset-Link. Das Löschen ist deshalb immer Stufe 2.

## Weitere Entscheidungen

- **JSON im Text statt Tool-Calling des Herstellers:** Jedes Modell kann einen JSON-Text liefern. Herstellerspezifisches Tool-Calling unterscheidet sich zwischen Anbietern (und bei Gemini je nach Modell bei der Mehrfach-Runde), und wir prüfen die Antwort sowieso selbst. Preis: etwas mehr Prompt-Text und ein eigener Parser.
- **Ein Schritt pro Modellaufruf, Verlauf als Text:** Das Modell bekommt in jedem Schritt das ganze Ticket und alle bisherigen Schritte. Das ist einfach, reproduzierbar und braucht keinen Gesprächszustand beim Anbieter.
- **Simulierte Umgebung statt echter Tenant:** Eine Entwickler-Lizenz gab es nicht. Die Simulation erlaubt reproduzierbare Auswertung (Zurücksetzen vor jedem Ticket) und ist sicher. Dafür ist sie einfacher als die Wirklichkeit.
- **Die Prüfung nach der Ausführung ist Code, nicht das Modell.** Sonst würde dasselbe Modell, das den Fehler gemacht hat, ihn auch bewerten.
- **PostgreSQL für Zustand, Aktionen und Protokoll:** Alles in einer Datenbank und Transaktion. Der Hash-Ketten-Eintrag nutzt eine Advisory Lock, damit parallele Tickets die Kette nicht verzweigen.

## Was als Nächstes verbessert werden sollte

1. Die Auswertung mit einem echten Sprachmodell laufen lassen und die Schwächen ansehen. Das Mock-Modell kann nur den Rahmen prüfen.
2. Adapter gegen einen Entwickler-Tenant testen.
3. Echte Anmeldung für Antragsteller und Prüfer statt einem gemeinsamen Token.
4. Letzten Hash der Audit-Kette extern ablegen.
5. Mehrere Anliegen pro Ticket besser abarbeiten (Schwäche in `k08` und `s05`).
