# Pointcloud Uploader Memory

Stand: 2026-03-14

## Git

- Repo: `Preloi/Pointcloud-Uploader`
- Aktueller Branch: `develop`
- Letzter gepushter Commit: `bd479d5` (`Add COPC upload and viewer support`)

## Aktueller Stand

- COPC-Upload ist im Uploader integriert.
- Klassischer Potree-Converter-Workflow bleibt parallel erhalten.
- Viewer 1.8.2 kann Potree-Projekte und COPC-Dateien laden.
- Live-`index.html` wurde ins Bucket `potreedronautix` hochgeladen.

## Wichtige Viewer-Details

- `server_viewer/index.html` ist die lokale Deploy-Kopie für den Server-Viewer.
- `deleted_projects.json` wird im Viewer jetzt von derselben Origin geladen, nicht mehr direkt von S3.
- Auf der "Projekt nicht verfügbar"-Seite wurde der Button entfernt.
- Stattdessen wird das Dronautix-Logo angezeigt und verlinkt auf `https://dronautix.at`.

## Wichtige Uploader-Details

- COPC-Dateien (`.copc.laz`) werden direkt nach S3 hochgeladen.
- Klassische `.las/.laz` laufen weiter über den Potree Converter.
- Der Löschablauf wurde robuster gemacht:
  - S3-Objekte werden zuerst gelöscht
  - `deleted_projects.json` wird dedupliziert aktualisiert
  - `projects_index.json` wird separat nachgezogen
  - Teilfehler werden explizit gemeldet

## Lokal geänderte, noch nicht gepushte Dateien

- `Dronautix_Pointcloud_Uploader_v7.py`
- `server_viewer/index.html`

## Temporäre lokale Dateien

- `bucket-cors.json` wurde für die S3-CORS-Konfiguration erzeugt
- `__pycache__/` ist lokal vorhanden

## Offene Hinweise

- Falls CloudFront noch alte Assets liefert, gezielt invalidieren.
- Für Live-Viewer-Änderungen ist `server_viewer/index.html` die maßgebliche Datei.
