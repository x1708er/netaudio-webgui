# netaudio Web-GUI — Feature-Erweiterung (Design)

Datum: 2026-06-14
Branch: `feature/gui-expansion`
Status: in Umsetzung (autonom, subagent-getrieben)

## Ziel

Die GUI von einer reinen Routing-Matrix zu einer vollwertigen Pro-Audio-Konsole
ausbauen. Vier vom Nutzer gewählte Bereiche (A Routing-Workflow, B Geräte-Konfig,
C Monitoring, E UX) in fünf unabhängig lieferbare Phasen zerlegt. Jede Phase ist
für sich shippbar, hat Tests und einen eigenen Commit.

## Bestand (vor dieser Arbeit)

- Routing-Matrix (TX-Spalten × RX-Zeilen), Klick = Abo an/aus, optimistisch + Toasts
- Geräte-Panel: Name, IP, Modell, Sample-Rate, Kanalzahl, Clock-Rolle
- Aktionen: Umbenennen (Gerät/Kanal), Identify, Reboot
- Clock-Leader-Anzeige, Neu-einlesen/Refresh, Token-Auth, Demo-Modus
- Backend: `build_*_argv()` (rein) → `NetaudioClient` → `DemoClient`-Spiegel
- Tests: argv-Builder (`test_netaudio_client.py`), Endpoints (`test_app.py`, `FakeClient`)

## Architektur-Leitplanken (für alle Phasen)

1. **Backend-Mutationen** folgen exakt dem Bestandsmuster:
   reine `build_<x>_argv(netaudio_bin, ...) -> list[str]` Funktion + `NetaudioClient`-
   Methode, die `_run_checked()` aufruft und danach `_after_change()` (bzw. `_after_change(device)`).
2. **DemoClient** muss jede neue Client-Methode spiegeln (sonst bricht Demo-Modus + Tests).
3. **Jede neue Client-Methode** bekommt einen argv-Builder-Test; **jeder neue Endpoint**
   einen `FakeClient`-Endpoint-Test. `FakeClient` in `test_app.py` um die neuen
   Methoden erweitern.
4. **Frontend** bleibt Vanilla-JS, terser Stil, deutsche UI-Texte, optimistische
   Updates + `toast()`. Keine Build-Tools, keine externen Assets.
5. **Sicherheit:** Server-seitige Validierung aller Enum-Werte (Sample-Rate, Bits,
   aes67 on/off, gain 1–5) — wie `set_channel_name` ungültige Typen ablehnt.
6. Alle Features müssen im **Demo-Modus** funktionieren (kein Hardware-Zugang im Test).

## Phase 1 — Routing-Workflow Quick Wins (Bereich A)

**Suche/Filter (Frontend-only):** Suchfeld im Header. Filtert Matrix (Zeilen +
Spalten) und Geräte-Panel live nach Geräte- oder Kanalnamen (case-insensitive,
Teilstring). Leeres Feld = alles sichtbar. Re-Filter nach jedem Re-Render.

**Matrix aufräumen (nutzt bestehende Endpoints):**
- RX-Gerät-Header bekommt eine kleine „✕"-Aktion → trennt alle RX-Kanäle dieses
  Geräts (Schleife über `DELETE /api/subscription` pro abonniertem RX-Kanal).
- „Alle trennen"-Button im Header (mit `confirm()`).
- Optimistisch wie Einzelklick; alle Mutationen über `mutationChain` serialisiert.

**Bulk-Routing Gerät→Gerät (Backend + UI):**
- `build_bulk_subscription_argv(bin, tx_device, rx_device, count=0, offset_tx=0, offset_rx=0)`
  → `[bin, "subscription", "add", "--tx", tx_device, "--rx", rx_device, "--count", str(count), ...]`
- `NetaudioClient.add_bulk_subscription(...)` + `_after_change(rx_device)`; DemoClient-Spiegel
  (1:1 über min(tx,rx)-Kanäle).
- Endpoint `POST /api/subscription/bulk` (Body: tx_device, rx_device, count, offset_tx, offset_rx).
- UI: Im RX-Geräte-Panel Button „⇄ Von Gerät…" → kleiner Dialog (TX-Gerät wählen) → Bulk-Abo.

## Phase 2 — Presets/Szenen (Bereich A)

Ganze Routing-Matrix als benannte Szene speichern und wiederherstellen.

- **Persistenz:** JSON-Datei, Pfad aus Settings (`NETAUDIO_GUI_PRESETS`, Default
  `~/.config/netaudio-webgui/presets.json`). Neues Modul `presets.py` (PresetStore):
  `list()`, `save(name, subscriptions)`, `get(name)`, `delete(name)`. Atomare Schreibvorgänge.
- **Snapshot-Inhalt:** Liste von `{rx_device, rx_channel(label), tx_device, tx_channel(label)}`
  aus dem aktuellen State. Beim Wiederherstellen werden Labels → Kanalnummern aufgelöst
  (über aktuellen State); fehlende Geräte/Kanäle werden übersprungen + im Ergebnis gemeldet.
- **Endpoints:** `GET /api/presets`, `POST /api/presets` (save: name + aktueller State-Snapshot
  serverseitig), `POST /api/presets/{name}/apply`, `DELETE /api/presets/{name}`.
  Apply: gewünschten Ziel-Zustand mit Ist-Zustand vergleichen, nur Diffs ausführen
  (fehlende Abos hinzufügen, überzählige entfernen) — kein Full-Teardown.
- **Tests:** PresetStore-Unit-Tests (tmp_path), Endpoint-Tests, Apply-Diff-Logik.
- **UI:** Presets-Leiste (Dropdown/Chips) im Header: speichern (Name-Prompt),
  anwenden, löschen. Toast mit „n verbunden, m getrennt, k übersprungen".

## Phase 3 — Geräte-Konfiguration (Bereich B)

Pro Gerät konfigurierbar; CLI kann es bereits. Server validiert Enums.

argv-Builder + Client-Methoden + DemoClient + Endpoints für:
- `sample-rate` (`build_sample_rate_argv` → `device config sample-rate <rate>`),
  erlaubt {44100,48000,88200,96000,176400,192000}
- `encoding` (bits ∈ {16,24,32})
- `latency` (ms, float/int > 0)
- `aes67` (on/off)
- `preferred-leader` (on/off)
- `channel gain` (`build_channel_gain_argv` → `channel gain <number> <level> --type tx|rx`, level 1–5)

Alle Geräte-Config-Befehle sind `--host`-basiert (wie name/identify/reboot).
Endpoints: `PUT /api/device/{host}/config/{key}` mit Body `{value: ...}` (sample-rate,
encoding, latency, aes67, preferred-leader); Gain: `PUT /api/device/{host}/channel/{number}/gain`
Body `{level, type}`.

**UI:** Im Geräte-Panel ein ausklappbarer „⚙ Konfiguration"-Bereich: Selects für
Sample-Rate/Encoding, Number-Input Latency, Toggles aes67/preferred-leader. Aktuelle
Werte aus State, wo verfügbar (sample_rate vorhanden; übrige optimistisch + Toast).
Gain: kleiner 1–5-Selector am Kanal-Chip (Kontextmenü/Long-press → vorerst Prompt
oder Inline-Select). Validierung serverseitig hart.

## Phase 4 — Monitoring/Status (Bereich C)

- **Clock-Panel:** Aus `get_state()` ableitbar (kein neues CLI). Header/Panel zeigt
  Leader + Liste der Follower (alle Geräte nach `clock_role`). Bereits-vorhandene
  Leader-Anzeige erweitern.
- **Geräte-Detailansicht:** Klick auf Gerät → Detail (alle Felder: server_name,
  online, model, sample_rate, clock_role, Kanalzahlen, IP). Nutzt vorhandene State-Daten;
  optional `device show` später.
- **Status-Legende:** Kleine Legende für Zellfarben (grün=verbunden, gelb=in Aufbau,
  rot=Fehler/unresolved, gestrichelt=ausstehend). Ein-/ausklappbar.
- **Offline-Darstellung:** Geräte mit `online=false` im Panel + Matrix optisch
  abgesetzt (gedimmt). `online` ist schon im State.

## Phase 5 — UX-Politur (Bereich E)

- **Theme-Toggle:** Dark (Default) / Light. CSS-Variablen sind schon zentral in
  `:root`; Light-Theme über `[data-theme="light"]`-Override. Wahl in `localStorage`.
- **Tastenkürzel:** `/` fokussiert Suche, `r` = Refresh, `Shift+R` = Neu-einlesen,
  `Esc` schließt Dialoge. Kleine Hilfe (`?`).
- **Konfig-Export/-Import:** Ganze Matrix als JSON herunterladen / hochladen
  (clientseitig; Import wendet wie ein Preset an). Wiederverwendet Phase-2-Apply-Logik.
- **Mobile-Layout:** `@media`-Breakpoints; Geräte-Panel unter die Matrix, Buttons
  größer (Touch). Matrix bleibt scrollbar.
- **Audit-Log:** In-Memory-Ringpuffer der letzten N Aktionen (Backend), Endpoint
  `GET /api/log`, kleine ein-/ausklappbare Log-Ansicht. (Nur Sitzungs-Log, keine Datei.)

## Reihenfolge & Abhängigkeiten

1 → 2 → 3 → 4 → 5 (sequenziell; alle Phasen teilen `app.py`/`netaudio_client.py`/
`app.js`/`style.css`, daher kein paralleles Editieren). Phase 5 (Export/Import)
nutzt die Apply-Diff-Logik aus Phase 2.

## Tests / Definition of Done je Phase

- `uv --project ~/src/netaudio-webgui run --group dev pytest -q` grün
- Neue Backend-Funktionalität durch argv-/Endpoint-/Store-Tests abgedeckt
- Demo-Modus funktioniert (DemoClient gespiegelt)
- Manuelle Sichtprüfung im Demo-Modus (Playwright/Screenshot) am Ende
- README um neue Features/Env-Vars ergänzt
