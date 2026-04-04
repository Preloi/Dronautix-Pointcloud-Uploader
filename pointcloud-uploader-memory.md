## Release 1.6.1

- Aktueller Release des Uploaders ist `1.6.1`.
- Neue Uploads erzeugen anonyme Kurz-Links nur noch mit der technischen Projekt-ID.
- `Dronautix_Pointcloud_Uploader_Setup_1.6.1.exe` und `latest-release.json` werden fuer diesen Release auf `Z:\03 Apps\Pointcloud uploader\` veroeffentlicht.
- Die UI-Versionsanzeige und das Setup ziehen ihre Versionsnummer zentral aus `app_version.py` und stehen jetzt auf `1.6.1`.

## Session Update 2026-04-04

### Aktueller Stand

- Die Uploader-App erzeugt neue Viewer-Links jetzt anonym nur noch mit der technischen Kurz-ID wie `?id=491d7a`.
- Der mitgelieferte `server_viewer/index.html` kann diese Kurz-IDs intern ueber `projects_index.json` aufloesen.
- Legacy-Links mit sprechendem Pfad bleiben weiterhin kompatibel.
- Fuer die echte Online-Version des Viewers ist weiterhin die separate Viewer-Repo mit ihrer `index.html` massgeblich; `server_viewer/index.html` dient hier nur als Test-/Deploy-Kopie.

### Konkrete technische Aenderungen

- In `Dronautix_Pointcloud_Uploader_v7.py` wurde die Link-Erzeugung fuer neue Uploads auf reine Kurz-IDs umgestellt.
- In `server_viewer/index.html` wurde die Projektauflosung fuer Kurz-IDs plus Legacy-Pfade umgesetzt.
- In `server_viewer/index.html` prueft die Geloescht-Logik jetzt sowohl Kurz-IDs als auch alte Pfad-Links.
- Release `1.6.1` wird aus dem aktuellen Stand gebaut und samt `latest-release.json` auf den Update-Share gelegt.
- In der Popup-Projektuebersicht wird `projects_by_id` beim Neuladen jetzt wieder korrekt aufgebaut, damit duplizierte Projekte sofort ueber "Im Browser oeffnen", "Link kopieren" und aehnliche Aktionen gefunden werden.
- Beide Projektansichten koennen ein ausgewaehltes Projekt bei Bedarf jetzt zusaetzlich direkt aus dem aktuellen Index ueber `id`, `link` oder `viewer_path` aufloesen, falls das lokale UI-Mapping nicht ausreicht.

# Pointcloud Uploader Memory

Stand: 2026-04-04

## Git

- Repo: `Preloi/Pointcloud-Uploader`
- Aktueller Branch: `develop`
- Aktueller Release: `1.6.1`

## Aktueller Stand

- COPC-Upload ist im Uploader integriert.
- Klassischer Potree-Converter-Workflow bleibt parallel erhalten.
- Viewer 1.8.2 kann Potree-Projekte und COPC-Dateien laden.
- Live-`index.html` wurde ins Bucket `potreedronautix` hochgeladen.
- Der Potree Converter ist in die App integriert (`bundled_tools/PotreeConverter`).
- Die App prueft beim Start `Z:\03 Apps\Pointcloud uploader\latest-release.json` auf neuere Versionen.
- Wenn eine neuere Version gefunden wird, fragt die App per Ja/Nein-Dialog, ob das Update jetzt installiert werden soll.
- EXE und Windows-Setup fuer Version `1.6.1` sind der aktuelle Release-Stand.
- EXE, Setup und `latest-release.json` fuer `1.6.1` wurden auf `Z:\03 Apps\Pointcloud uploader\` aktualisiert.
- Kuenftige Versionen werden normalerweise in `0.1`-Schritten erhoeht; dieser Hotfix wurde auf ausdruecklichen Wunsch als `1.6.1` veroeffentlicht.
- Die Versionsnummer steht unten in der Statusleiste der App.
- Setup, App-Version und `latest-release.json` werden aus derselben zentralen Versionsquelle abgeleitet.
- Vor jedem Build werden die lokalen Build-Artefakte in `build/`, `dist/` und `Output/` geloescht, damit immer nur der aktuelle Build-Stand vorliegt.
- Nach dem Bau des Setups immer fragen, ob die neuen Release-Dateien auf den Share geladen werden sollen.
- Klassische LAS/LAZ-Konvertierungsdaten werden nach erfolgreichem Upload lokal wieder geloescht.

## Wichtige Viewer-Details

- `server_viewer/index.html` ist nur die lokale Test-/Deploy-Kopie fuer den Viewer; live massgeblich ist die separate Viewer-Repo mit ihrer `index.html`.
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
- Die Versionsanzeige in der App steht auf `1.6.1`.

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
- `Output/Dronautix_Pointcloud_Uploader_Setup_1.6.1.exe`
- `Output/latest-release.json`
- `Z:\03 Apps\Pointcloud uploader\latest-release.json`
- `Z:\03 Apps\Pointcloud uploader\Dronautix_Pointcloud_Uploader_Setup_1.6.1.exe`

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
- Fuer echte Live-Viewer-Aenderungen ist die `index.html` der separaten Viewer-Repo die massgebliche Datei, nicht `server_viewer/index.html`.
- Nach diesem Hotfix wieder auf das uebliche `0.1`-Schema zurueckgehen, ausser es wird bewusst erneut ein Patch-Release gewuenscht.
- Setup nutzt `CloseApplications` und deinstalliert aeltere Versionen vor der Neuinstallation.
- Setup beendet laufende App automatisch (`KillRunningApp`) und raeumt `_MEI*` Temp-Ordner auf.
- Das verbesserte Update-/Setup-Verhalten greift zuverlaessig erst ab installierter Version `1.2`.
