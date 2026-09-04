# Market Intelligence

Taeglicher E-Mail-Digest und Sofort-Alerts zum deutschen Gesundheitsmarkt.
Zweck: Marktexpertise aufbauen, ohne selbst zu suchen. Sekundaerzweck: ein
durchsuchbares Langzeitarchiv.

```
Cron (06:00 CET)          Cron (alle 2h, 08:00–20:00)
  Collector   RSS + News-Queries + Scraper    Collector (nur Alert-Quellen)
  Dedupe      SQLite, URL-Hash + Titel        Trigger-Pruefung
  Prefilter   Haiku, Relevanz 0–10            bei Treffer: sofortige E-Mail
  Digest      Sonnet, 5–8 Items
  Versand     E-Mail
```

Stack: Python, SQLite, GitHub Actions als Scheduler, Resend oder SMTP fuer den
Versand. Bewusst identisch zum geplanten B2B-Outreach-System, damit spaeter ein
gemeinsames Dashboard moeglich ist.

---

## Stand der Bauschritte

| # | Schritt | Stand |
|---|---|---|
| 1 | Feed-URLs verifizieren, Liste in `sources.yaml` | **Werkzeug fertig, Lauf offen** — siehe unten |
| 2 | Collector + SQLite-Schema, erstmal nur speichern | fertig |
| 3 | Drei Tage Rohdaten, dann Prefilter kalibrieren | Code fertig, **Kalibrierung braucht echte Daten** |
| 4 | Digest-Generierung + E-Mail-Versand | fertig |
| 5 | Alert-Pfad | fertig |
| 6 | Archiv-Abfrage + Monatsverdichtung | fertig |
| 7 | Wettbewerber-Website-Diff (woechentlich) | fertig |

### Bauschritt 1 ist noch nicht abgeschlossen

Die Spec sagt: *nicht raten — jede Quelle einmal manuell pruefen.* Genau das ist
noch offen. In `sources.yaml` steht bei jeder Quelle `status: unverified`; die
URLs unter `candidates` sind begruendete Kandidaten, keine geprueften Feeds.

**Der Collector ueberspringt unverifizierte Quellen.** Ohne den Verifikationslauf
sammelt das System nichts — das ist Absicht und keine Fehlkonfiguration.

```bash
pip install -r requirements.txt
python -m mi verify-sources
```

Der Lauf probiert je Quelle die Kandidaten durch, liest die Uebersichtsseite auf
`<link rel="alternate" type="application/rss+xml">` aus, testet gaengige Pfade
und akzeptiert nur, was feedparser als Feed mit mindestens einem Eintrag liest —
eine HTML-Fehlerseite mit Status 200 faellt durch. Das Ergebnis landet in
`sources.lock.yaml`:

- `verified` — Feed gefunden, Quelle laeuft ab sofort mit.
- `no_feed` — kein Feed vorhanden. Dann greift der HTML-Scraper, und der
  `html.item_selector` in `sources.yaml` will einmal im Browser gegengeprueft
  werden. Ein Selektor, der nichts trifft, faellt sonst erst im Digest auf.
- `broken` — Seite nicht erreichbar. Von Hand nachsehen.

`sources.yaml` bleibt handgepflegt (samt Kommentaren), die Lockdatei ist
maschinengeschrieben — wie bei einem Paketmanager.

### Bauschritt 3 nicht ueberspringen

Ein schlecht kalibrierter Filter ist der einzige Weg, wie dieses System
scheitert. Deshalb: drei Tage nur sammeln und bewerten, kein Versand.

```bash
python -m mi collect       # taeglich, drei Tage lang
python -m mi prefilter
python -m mi calibrate     # danach
```

`mi calibrate` zeigt die Score-Verteilung, wie viele Items pro Tag bei welcher
Schwelle im Digest laendeten, die Trefferquote je Quelle und Stichproben samt
der Begruendung, die das Modell vergeben hat. Aus dem Bericht ergibt sich der
Wert fuer `MI_DIGEST_MIN_SCORE` — die 4 aus der Spec ist ein Startwert, keine
Messung. Faustregel: 5–8 Items pro Tag sollen den Digest fuellen.

---

## Einrichtung

```bash
pip install -r requirements.txt
cp .env.example .env          # Schluessel eintragen
python -m mi verify-sources   # Bauschritt 1
python -m mi status
```

`MI_MAIL_BACKEND` steht per Default auf `console`: die Mail geht nach stdout,
verschickt wird nichts. So kann ein halb konfiguriertes System niemandem Post
schicken. Fuer den echten Versand auf `resend` oder `smtp` umstellen.

### Befehle

| Befehl | Was er tut |
|---|---|
| `mi verify-sources` | Feeds pruefen, `sources.lock.yaml` schreiben |
| `mi collect [--cadence daily\|alerts\|weekly]` | sammeln und speichern |
| `mi prefilter` | unbewertete Items durch Haiku schicken |
| `mi calibrate [--days 7]` | Score-Verteilung und Schwellen-Bericht |
| `mi digest [--dry-run]` | Marktbrief bauen und verschicken |
| `mi alerts [--dry-run]` | Trigger pruefen, bei Treffer sofort melden |
| `mi ask "Stand GOÄneu-Verhandlungen"` | Archiv befragen, mit Quellenlinks |
| `mi monthly [--month YYYY-MM]` | Vormonat je Themenstrang verdichten |
| `mi competitors` | Wettbewerberseiten diffen |
| `mi status` | Quellenzustand, Archivgroesse, Kosten, letzte Laeufe |

`--dry-run` gibt aus, statt zu verschicken. Erste Wahl beim Ausprobieren.

---

## Betrieb ueber GitHub Actions

Vier Workflows unter `.github/workflows/`: Digest (taeglich), Alerts (alle zwei
Stunden), Monatsverdichtung (Monatserster), Wettbewerber-Check (montags).

Benoetigte Secrets: `ANTHROPIC_API_KEY`, `MI_MAIL_FROM`, `MI_MAIL_TO` und je
nach Backend `RESEND_API_KEY` oder die `SMTP_*`. Optionale Repository-Variablen:
`MI_DIGEST_MIN_SCORE`, `MI_ALERT_MIN_SCORE`, `MI_MAX_ALERTS_PER_DAY`.

### Wo die Datenbank lebt

Actions-Runner sind fluechtig — nach dem Job ist die SQLite-Datei weg. Der
Archivzweck braucht sie aber dauerhaft. `scripts/db_sync.sh` legt sie deshalb in
einen verwaisten Branch `mi-data`, der bei jedem Lauf mit **einem** Commit
ueberschrieben wird; alte Blobs werden unerreichbar und von GitHub weggeraeumt,
das Repo bleibt klein. Alle Workflows teilen sich `concurrency: mi-data`, damit
kein Lauf den anderen ueberholt, und sichern die DB auch dann, wenn der Schritt
davor fehlgeschlagen ist.

Lokale Kopie des Archivs:

```bash
git fetch origin mi-data && git show origin/mi-data:mi.db > data/mi.db
```

Wenn die Datei zu gross wird (Faustregel ab ~200 MB): Litestream gegen S3/R2,
Turso/libSQL, oder ein kleiner Server mit Cron statt Actions. Der Rest des
Systems bleibt davon unberuehrt — nur `MI_DB_PATH` zeigt woandershin.

### Zeitzonen

Actions rechnet in UTC und kennt keine Sommerzeit. Der Digest-Workflow feuert
deshalb zweimal (04:00 und 05:00 UTC); der jeweils "falsche" Lauf findet nichts
Neues und kostet fast nichts. Sauberer waere ein Guard im Job — bewusst nicht
gebaut, weil zwei Cron-Zeilen weniger Code sind als eine Zeitzonenpruefung.

---

## Alert-Logik

Ein Sofort-Alert braucht **Score >= 8 UND einen Trigger**. Beides, nicht eines
von beiden.

**Watchlist** (ein Treffer loest immer aus): Schön Klinik · ADK GmbH ·
Kreiskrankenhaus Ehingen · Dedalus · ORBIS · Doctario · MediCoda · Qodia ·
Avelios · Nelly · Felia

**Ereignisklassen**

1. Offizielle Aeusserung zu GOÄneu von BÄK, PKV-Verband oder BMG
2. Gesetzesvorhaben mit Bezug auf Privatliquidation erreicht neue Verfahrensstufe
3. Wettbewerber meldet Finanzierungsrunde, Klinikpartner oder Produktlaunch
4. Insolvenz oder Uebernahme einer Klinikgruppe mit >5 Standorten
5. Jedes Krankenhaus in Baden-Wuerttemberg mit Insolvenz/Traegerwechsel

Maximal drei Alerts pro Tag. Was darueber liegt, bleibt unversendet und wandert
automatisch in den Digest — sonst stumpft der Kanal ab.

Zwei Entscheidungen, die in der Spec offen waren:

- **Klasse 4 ohne Standortangabe** loest aus. Wenn im Text keine Zahl steht, ist
  ein moeglicher Fehlalarm billiger als eine verpasste Konzernuebernahme — der
  Score-Filter davor hat die Meldung ohnehin schon als hochrelevant eingestuft.
- **Bereits gealertete Items erscheinen weiterhin im Digest.** Ein Tagesbrief,
  der die groesste Meldung des Tages auslaesst, waere als Protokoll unbrauchbar.

Alle Muster matchen sowohl "Tübingen" als auch "Tuebingen": Text und Muster
werden vor dem Vergleich transliteriert, weil Feeds mal so und mal so schreiben.

---

## Archiv

```sql
items(id, url, url_hash, title, source, published_at, fetched_at,
      raw_text, summary, score, topics, entities, alerted, ...)
entities(id, name, type, first_seen, last_seen, mention_count)
monthly_briefs(id, month, topic, text)
```

Jedes Item wird gespeichert, auch Score-2-Rauschen — die Rueckschau braucht
Vollstaendigkeit. Duplikate werden nicht geloescht, sondern ueber `dedupe_of`
verknuepft. `raw_text` ist auf 20.000 Zeichen gedeckelt (`MI_RAW_TEXT_LIMIT`),
damit die Datei handhabbar bleibt.

Ueber `items` liegt ein FTS5-Volltextindex, den Trigger synchron halten. `mi ask`
sucht darin, legt die juengsten Monatsbriefe als Kontext daneben und laesst
ausschliesslich daraus antworten — mit Quellennummern und Links. Wenn das Archiv
die Frage nicht hergibt, sagt die Antwort das, statt zu raten. Genau dafuer
existiert das Archiv: eine belegbare Antwort fuer Investorengespraeche,
Klinik-Pitches und EXIST-Zwischenberichte.

---

## Modelle und Kosten

| Aufgabe | Modell | Frequenz |
|---|---|---|
| Prefilter | `claude-haiku-4-5` | ~100–200 Items/Tag, gebuendelt zu je 10 |
| Digest + Einordnung | `claude-sonnet-5` | einmal taeglich |
| Monatsverdichtung | `claude-sonnet-5` | einmal im Monat |
| `mi ask` | `claude-opus-5` | auf Zuruf |

Ueberschreibbar per `MI_MODEL_PREFILTER` und Geschwistern. Das Interessensprofil
steht als gecachter Systemprompt vor jedem Prefilter-Buendel, wird also nicht
hundertmal am Tag neu bezahlt. Jeder Lauf schreibt Tokenzahl und geschaetzte
Kosten nach `runs`; `mi status` zeigt die Summe der letzten 30 Tage. Realistisch
sind wenige Euro im Monat, GitHub Actions bleibt im Free Tier.

---

## Entwicklung

```bash
pip install -r requirements-dev.txt
python -m pytest -q
```

Die Tests decken die Stellen ab, an denen ein Fehler teuer ist: Dedupe,
Alert-Trigger (jede Ereignisklasse einzeln, mitsamt der Faelle, die *nicht*
ausloesen duerfen), die Feed-Erkennung, und dass der Digest die URL aus der
Datenbank nimmt statt aus der Modellantwort.

### Was bewusst nicht gebaut ist

- **LinkedIn-Firmenseiten.** Automatisiertes Abrufen verstoesst gegen die ToS.
  Wer die Seiten beobachten will, macht das manuell oder ueber die offizielle
  Marketing-API.
- **Handelsblatt Inside Digital Health.** Paywall; steht als `enabled: false` in
  `sources.yaml` und wartet auf ein Abo.
- **Bundestag DIP** laeuft vorerst als Scraper. Es gibt eine offizielle API mit
  Key — beim Verifikationslauf pruefen, ob sich der Wechsel lohnt.
