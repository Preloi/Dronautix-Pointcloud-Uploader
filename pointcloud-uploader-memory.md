# Pointcloud Uploader Memory

Stand: 2026-04-02

## Git

- Repo: `Preloi/Pointcloud-Uploader`
- Aktueller Branch: `develop`
- Aktueller Release: `1.5`

## Aktueller Stand

- COPC-Upload ist im Uploader integriert.
- Klassischer Potree-Converter-Workflow bleibt parallel erhalten.
- Viewer 1.8.2 kann Potree-Projekte und COPC-Dateien laden.
- Live-`index.html` wurde ins Bucket `potreedronautix` hochgeladen.
- Der Potree Converter ist in die App integriert (`bundled_tools/PotreeConverter`).
- Die App prueft beim Start `Z:\03 Apps\Pointcloud uploader\latest-release.json` auf neuere Versionen.
- Wenn eine neuere Version gefunden wird, fragt die App per Ja/Nein-Dialog, ob das Update jetzt installiert werden soll.
- EXE und Windows-Setup fuer Version `1.5` sind der aktuelle Release-Stand.
- EXE, Setup und `latest-release.json` fuer `1.5` wurden auf `Z:\03 Apps\Pointcloud uploader\` aktualisiert.
- Kuenftige Versionen werden in `0.1`-Schritten erhoeht (`1.0`, `1.1`, `1.2`, `1.3`, `1.4`, `1.5`, ...).
- Die Versionsnummer steht unten in der Statusleiste der App.
- Setup, App-Version und `latest-release.json` werden aus derselben zentralen Versionsquelle abgeleitet.
- Vor jedem Build werden die lokalen Build-Artefakte in `build/`, `dist/` und `Output/` geloescht, damit immer nur der aktuelle Build-Stand vorliegt.
- Nach dem Bau des Setups immer fragen, ob die neuen Release-Dateien auf den Share geladen werden sollen.
- Klassische LAS/LAZ-Konvertierungsdaten werden nach erfolgreichem Upload lokal wieder geloescht.

## Wichtige Viewer-Details

- `server_viewer/index.html` ist die lokale Deploy-Kopie fuer den Server-Viewer.
- `deleted_projects.json` wird im Viewer jetzt von derselben Origin geladen, nicht mehr direkt von S3.
- Auf der "Projekt nicht verfuegbar"-Seite wurde der Button entfernt.
- Stattdessen wird das Dronautix-Logo angezeigt und verlinkt auf `https://dronautix.at`.
- Nach 30 Tagen wird nicht mehr die explizite Loeschmeldung gezeigt, sondern bei fehlenden Dateien eine allgemeine Nicht-verfuegbar-Seite.

## Wichtige Uploader-Details

- COPC-Dateien (`.copc.laz`) werden direkt nach S3 hochgeladen.
- Klassische `.las/.laz` laufen weiter ueber den Potree Converter.
- Die Originaldatei bleibt lokal erhalten.
- Der erzeugte Potree-Output ist nur temporaer und wird nach erfolgreichem Upload per Cleanup entfernt.
- Der Loeschablauf wurde robuster gemacht:
  - S3-Objekte werden zuerst geloescht
  - `deleted_projects.json` wird dedupliziert aktualisiert
  - `projects_index.json` wird separat nachgezogen
  - Teilfehler werden explizit gemeldet
- Die Versionsanzeige in der App steht auf `1.5`.

## Aenderungen in Version 1.5

### UI-Anpassungen
- Drag-and-Drop-Feld zeigt nach Dateiauswahl oder Drop jetzt den kompletten Dateipfad an
- Drag-and-Drop-Feld im Hauptfenster wurde etwas kompakter gemacht
- Die Kopfzeile "Upload und Verwaltung im selben Fenster" wurde aus dem Hauptfenster entfernt
- Projektuebersicht-Ueberschrift lautet jetzt: "Bestehende Projekte oeffnen, loeschen, austauschen oder duplizieren"
- Duplizieren-Dialoge wurden vergroessert, damit die Buttons sicher sichtbar sind
- Die Farbe aller "Duplizieren"-Buttons wurde an die Standard-Aktionsfarbe der App angepasst

## Aenderungen in Version 1.4

### UI-Refactoring
- Neues View-System: `show_main_view()` mit `app_views` und `nav_buttons` Dictionaries
- Singleton-Fenster: `focus_existing_window()` verhindert doppelte Projektuebersicht-/Einstellungsfenster
- Robuste Widget-Pruefung: `widget_exists()` faengt `TclError` bei zerstoerten Widgets ab
- UI-Kontext-System: `ui_log()`, `ui_set_step()`, `ui_set_progress()`, `ui_set_detail()`, `ui_reset_progress()` koennen optional in lokale Dialoge statt ins Hauptfenster schreiben

### Ordnernamen-Sanitierung verbessert
- Nutzt jetzt `unicodedata.normalize()` fuer vollstaendige Unicode-Behandlung (nicht nur ae/oe/ue)

### Setup-Verbesserungen (Inno Setup)
- `KillRunningApp()` Prozedur: Beendet laufende App vor Installation per `taskkill`
- Wartet 3 Sekunden nach Kill (per Ping-Trick) bis Dateien freigegeben
- Raeumt alte PyInstaller `_MEI*` Temp-Ordner im `%TEMP%` auf

## Aenderungen April 2026 (in 1.5 enthalten)

### Projekt-Duplizieren-Funktion
- Neuer "Duplizieren"-Button in der Projektuebersicht
- Dialog mit Eingabefeldern fuer neuen Kunden- und Projektnamen (vorausgefuellt mit Original + " (Kopie)")
- `duplicate_project_process()` kopiert alle S3-Dateien per `s3_client.copy_object()` zu neuem Pfad
- Eigene ID (`uuid.uuid4().hex[:6]`), eigener S3-Pfad, eigener Viewer-Link
- Fortschrittsanzeige im Dialog, Index + CSV werden aktualisiert
- Bestehende Projekte/Links bleiben komplett unberuehrt
- Funktion existiert in beiden UI-Varianten (Popup-Fenster + eingebettete Hauptansicht)

### Suche verbessert
- Projektuebersicht-Suche ist jetzt case-insensitive (Gross-/Kleinschreibung wird ignoriert)
- Suche durchsucht sowohl Projektname als auch Kundenname

### Umlaut-Bereinigung
- Alle doppelt-encodierten UTF-8-Sequenzen im Quellcode repariert
- UI-Texte (Buttons, Labels, Dialoge, Messageboxen) verwenden echte deutsche Umlaute (oe, ue, ae, ss)
- Interne Log-Messages und Ordnernamen bleiben ASCII-sicher
- Kaputte Emoji-Reste durch sinnvollen Text ersetzt

## Release-Dateien

- `dist/Dronautix_Pointcloud_Uploader.exe`
- `Output/Dronautix_Pointcloud_Uploader_Setup_1.5.exe`
- `Output/latest-release.json`
- `Z:\03 Apps\Pointcloud uploader\latest-release.json`
- `Z:\03 Apps\Pointcloud uploader\Dronautix_Pointcloud_Uploader_Setup_1.5.exe`

## Git-relevante Dateien fuer das Release

- `app_version.py`
- `version_info.txt`
- `installer_version.iss`
- `latest-release.json`
- `build_exe.py`
- `Dronautix_Pointcloud_Uploader.iss`
- `bundled_tools/PotreeConverter/`
- `.gitignore`

## Offene Hinweise

- Falls CloudFront noch alte Assets liefert, gezielt invalidieren.
- Fuer Live-Viewer-Aenderungen ist `server_viewer/index.html` die massgebliche Datei.
- Nach `1.5` weiter nur in `0.1`-Schritten erhoehen, nicht in Patch-Versionen wie `1.5.1`.
- Setup nutzt `CloseApplications` und deinstalliert aeltere Versionen vor der Neuinstallation.
- Setup beendet laufende App automatisch (`KillRunningApp`) und raeumt `_MEI*` Temp-Ordner auf.
- Das verbesserte Update-/Setup-Verhalten greift zuverlaessig erst ab installierter Version `1.2`.
