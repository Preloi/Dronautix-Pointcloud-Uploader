# Codex-Anweisung: Uploader-Erweiterung fuer Multi-Punktwolken-Projekte

## Ziel
Erweitere den Dronautix Pointcloud Uploader so, dass ein Projekt optional mehrere Punktwolken enthalten kann. Der bestehende Einzel-Punktwolken-Workflow muss vollstaendig rueckwaertskompatibel bleiben. Alte Projekte, alte Viewer-Links und alte `projects_index.json`-Eintraege duerfen nicht brechen.

Der neue WebGL2-Viewer kann mehrere Punktwolken darstellen, wenn sie einzeln geladen und mit `viewer.scene.addPointCloud(pointcloud)` zur Szene hinzugefuegt werden. Der Objektbaum erzeugt dann automatisch pro Punktwolke einen eigenen Eintrag unter `Punktwolken`, inklusive Checkbox, Auswahl und Doppelklick-Zoom.

## Nicht aendern
- Keine bestehenden Einzelprojekt-Indexfelder entfernen.
- Keine bestehenden Projekt-IDs oder Linkformate invalidieren.
- Keine bestehenden Upload-Zielpfade fuer Einzelprojekte aendern.
- Keine Punktwolken-Daten in Git committen.
- Keine Viewer-Code-Aenderungen in diesem Uploader-Repo vornehmen, ausser es existiert hier eine lokale Kopie nur fuer Deployment/Packaging.

## Rueckwaertskompatibilitaet
Der Uploader muss weiterhin Eintraege in dieser bisherigen Form schreiben koennen:

```json
{
  "datum": "2026-04-26T12:00:00",
  "kunde": "Demo",
  "id": "d148fa",
  "projekt": "Freileitung Bodenabstand Visualisierung",
  "format": "potree",
  "link": "https://pointcloud.dronautix.at/index.html?id=d148fa",
  "viewer_path": "demo/d148fa/freileitung_bodenabstand_visualisierung",
  "s3_path": "pointclouds/demo/d148fa/freileitung_bodenabstand_visualisierung"
}
```

Die Lade-/Auswertelogik soll immer diese Regel respektieren:

```text
Wenn project.pointclouds ein Array mit mindestens einem Eintrag ist:
    Multi-Punktwolken-Projekt behandeln.
Sonst:
    Bestehendes Einzelpunktwolken-Projekt ueber viewer_path oder s3_path behandeln.
```

## Neues Index-Schema fuer Multi-Projekte
Fuer Multi-Punktwolken-Projekte soll `projects_index.json` optional ein Feld `pointclouds` erhalten. Der Top-Level-Eintrag bleibt ein normales Projekt mit einer einzigen Projekt-ID und einem einzigen Link.

Beispiel:

```json
{
  "datum": "2026-04-26T12:00:00",
  "kunde": "Demo",
  "id": "multi123",
  "projekt": "Leitung Gesamtprojekt",
  "format": "multi",
  "link": "https://pointcloud.dronautix.at/index.html?id=multi123&name=Demo%20-%20Leitung%20Gesamtprojekt",
  "viewer_path": "demo/multi123/leitung_gesamtprojekt",
  "s3_path": "pointclouds/demo/multi123/leitung_gesamtprojekt",
  "pointclouds": [
    {
      "name": "Bestand",
      "format": "potree",
      "viewer_path": "demo/multi123/leitung_gesamtprojekt/bestand",
      "s3_path": "pointclouds/demo/multi123/leitung_gesamtprojekt/bestand"
    },
    {
      "name": "Neuplanung",
      "format": "potree",
      "viewer_path": "demo/multi123/leitung_gesamtprojekt/neuplanung",
      "s3_path": "pointclouds/demo/multi123/leitung_gesamtprojekt/neuplanung"
    },
    {
      "name": "Kontrolle COPC",
      "format": "copc",
      "viewer_path": "demo/multi123/leitung_gesamtprojekt/kontrolle/source.copc.laz",
      "s3_path": "pointclouds/demo/multi123/leitung_gesamtprojekt/kontrolle/source.copc.laz"
    }
  ]
}
```

## Pfadregeln
Der Uploader soll pro Punktwolke einen eigenen Unterordner oder eine eigene COPC-Datei erzeugen.

Potree-Beispiel:

```text
pointclouds/<kunde_slug>/<projekt_id>/<projekt_slug>/<cloud_slug>/metadata.json
pointclouds/<kunde_slug>/<projekt_id>/<projekt_slug>/<cloud_slug>/octree.bin
pointclouds/<kunde_slug>/<projekt_id>/<projekt_slug>/<cloud_slug>/hierarchy.bin
```

COPC-Beispiel:

```text
pointclouds/<kunde_slug>/<projekt_id>/<projekt_slug>/<cloud_slug>/source.copc.laz
```

`viewer_path` soll ohne fuehrendes `pointclouds/` gespeichert werden. `s3_path` darf weiterhin den vollstaendigen S3/Hosting-Pfad inklusive `pointclouds/` enthalten.

## Uploader-UI/Workflow
Baue den Uploader so um, dass der Nutzer entweder den bisherigen Einzelupload oder einen neuen Multi-Upload nutzen kann.

Minimaler sinnvoller Workflow:

1. Projekt-Metadaten wie bisher erfassen: Kunde, Projektname, ID/Slug, Datum.
2. Modus auswaehlen: `Einzelne Punktwolke` oder `Mehrere Punktwolken`.
3. Bei `Einzelne Punktwolke`: alles bleibt wie bisher.
4. Bei `Mehrere Punktwolken`: Liste von Punktwolken verwalten.
5. Pro Punktwolke erfassen:
   - Anzeigename, z. B. `Bestand`, `Neuplanung`, `Befliegung 1`, `Kontrolle`.
   - Eingabedatei oder Eingabeordner.
   - Format/Zieltyp: `potree` oder `copc`.
   - Optional: Start sichtbar ja/nein, falls spaeter vom Viewer genutzt.
6. Jede Punktwolke einzeln konvertieren/validieren/hochladen.
7. Danach genau einen Projektlink fuer das Gesamtprojekt erzeugen.

## Validierung
Vor dem Upload pruefen:

- Es gibt mindestens eine Punktwolke.
- Jeder Punktwolken-Eintrag hat einen eindeutigen `name` innerhalb des Projekts.
- Jeder Punktwolken-Eintrag hat einen stabilen `cloud_slug`/Zielordner.
- Kein Zielordner wird von zwei Punktwolken gleichzeitig genutzt.
- Fuer Potree-Ziele existiert nach der Konvertierung `metadata.json`.
- Fuer COPC-Ziele existiert nach der Konvertierung `source.copc.laz` oder der konkrete `.copc.laz`-Pfad.
- Warnung anzeigen, wenn mehrere Punktwolken vermutlich unterschiedliche Koordinatensysteme/Offsets haben.

Wichtig: Der Viewer kann mehrere Punktwolken nur sinnvoll gemeinsam anzeigen, wenn sie raeumlich im gleichen Koordinaten-/Referenzsystem liegen oder passende Transformationen hinterlegt werden.

## Optionales Transformationsschema
Noch nicht zwingend fuer die erste Umsetzung, aber zukunftssicher vorbereiten:

```json
{
  "name": "Neuplanung",
  "format": "potree",
  "viewer_path": "demo/multi123/projekt/neuplanung",
  "s3_path": "pointclouds/demo/multi123/projekt/neuplanung",
  "visible": true,
  "transform": {
    "position": [0, 0, 0],
    "rotation": [0, 0, 0],
    "scale": [1, 1, 1]
  }
}
```

Der erste Viewer-Umbau kann `transform` ignorieren oder nur dann anwenden, wenn vorhanden. Der Uploader soll das Feld nur schreiben, wenn es wirklich gesetzt wird.

## deleted_projects.json
Die bestehende Loeschlogik muss weiter funktionieren.

Empfehlung:

- Top-Level-Projekt weiterhin in `deleted_projects.json` markieren.
- Bei Multi-Projekten optional alle Child-Pfade mitschreiben, damit Cleanup und Diagnose einfacher werden.

Beispiel:

```json
{
  "id": "multi123",
  "kunde": "Demo",
  "projekt": "Leitung Gesamtprojekt",
  "s3_path": "pointclouds/demo/multi123/leitung_gesamtprojekt",
  "deleted_at": "2026-04-26T12:00:00",
  "child_s3_paths": [
    "pointclouds/demo/multi123/leitung_gesamtprojekt/bestand",
    "pointclouds/demo/multi123/leitung_gesamtprojekt/neuplanung"
  ],
  "original_link": "https://pointcloud.dronautix.at/index.html?id=multi123&name=Demo%20-%20Leitung%20Gesamtprojekt"
}
```

Der Viewer muss fuer Rueckwaertskompatibilitaet weiterhin bereits durch `id` oder Top-Level-`s3_path` sperren koennen.

## Akzeptanzkriterien
Die Umsetzung im Uploader ist fertig, wenn:

- Alte Einzeluploads erzeugen byte-/schema-kompatible Eintraege wie bisher.
- Ein Multi-Projekt erzeugt genau einen Top-Level-Projekteintrag mit `pointclouds[]`.
- Jede enthaltene Punktwolke wird in einen eigenen Zielpfad hochgeladen.
- Der erzeugte Link zeigt weiterhin nur auf eine Projekt-ID, nicht auf einzelne Clouds.
- `projects_index.json` bleibt valides JSON.
- `deleted_projects.json` bleibt valides JSON.
- Bestehende Viewer-Links funktionieren unveraendert.
- Ein WebGL2-Viewer, der `pointclouds[]` unterstuetzt, kann alle Clouds laden und einzeln im Objektbaum anzeigen.
- Lokale Test-/Ausgabeordner, grosse Punktwolken und Build-Artefakte werden nicht versehentlich in Git aufgenommen.

## Empfohlene Testfaelle
1. Einzelupload Potree wie bisher.
2. Einzelupload COPC wie bisher.
3. Multi-Projekt mit zwei Potree-Punktwolken.
4. Multi-Projekt mit Potree + COPC gemischt.
5. Multi-Projekt loeschen und pruefen, ob alle Child-Pfade korrekt behandelt werden.
6. Bestehende alte `projects_index.json` ohne `pointclouds[]` laden und unveraendert speichern.
7. Fehlerfall: zwei Punktwolken mit gleichem Anzeigenamen.
8. Fehlerfall: zwei Punktwolken mit gleichem Zielordner.

## Implementationshinweis fuer Codex
Vor Codeaenderungen im Uploader zuerst die bestehenden Funktionen finden, die aktuell diese Aufgaben erledigen:

- Projekt-ID/Slug erzeugen.
- Potree/COPC-Konvertierung starten.
- Upload-Zielpfade bauen.
- `projects_index.json` lesen/schreiben.
- `deleted_projects.json` lesen/schreiben.
- Viewer-Link erzeugen.
- UI-Formularwerte validieren.

Danach die Erweiterung moeglichst klein und rueckwaertskompatibel einbauen. Falls moeglich, zuerst reine Datenmodell-/Helper-Funktionen einfuehren, dann UI, dann Upload-Orchestrierung, dann Tests.
