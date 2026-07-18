# Viewer-Handoff: Deaktivierte Projekte über Direktlink sperren

## Problem

Der Dronautix Pointcloud Uploader deaktiviert ein Projekt, indem er es in
`projects_index.json` aus `projects` nach `disabled_projects` verschiebt. Ein
bereits bekannter Viewer-Link wie
`https://pointcloud.dronautix.at/index.html?id=<project-id>` öffnet das Projekt
aktuell trotzdem weiterhin.

Der Viewer muss den Status aus `projects_index.json` auswerten und den
Ladevorgang für deaktivierte Projekte beenden.

## Datenvertrag

Aktive Projekte stehen ausschließlich in `projects`:

```json
{
  "projects": [
    {
      "id": "active-project",
      "projekt": "Aktives Projekt",
      "viewer_path": "kunde/active-project/projekt"
    }
  ],
  "disabled_projects": []
}
```

Beim Deaktivieren wird der vollständige Projekteintrag nach
`disabled_projects` verschoben. `link`, `viewer_path` und `s3_path` bleiben für
die spätere Reaktivierung erhalten:

```json
{
  "projects": [],
  "disabled_projects": [
    {
      "id": "disabled-project",
      "projekt": "Deaktiviertes Projekt",
      "link": "https://pointcloud.dronautix.at/index.html?id=disabled-project",
      "viewer_path": "kunde/disabled-project/projekt",
      "s3_path": "pointclouds/kunde/disabled-project/projekt",
      "disabled_at": "2026-07-18T12:30:00"
    }
  ]
}
```

## Gewünschtes Verhalten

Beim Aufruf mit `?id=<project-id>`:

1. `projects_index.json` aktuell laden.
2. Die ID zuerst in `disabled_projects` suchen.
3. Bei einem Treffer eine neutrale Fehlerseite anzeigen, zum Beispiel:
   **„Dieses Projekt wurde deaktiviert.“**
4. In diesem Fall keine Projektmetadaten, `cloud.js`, COPC-Datei, Hierarchie-
   oder Punktwolkendaten laden und keinen Potree-/Viewer-Start ausführen.
5. Nur Projekte aus `projects` regulär öffnen.
6. Ist die ID in keiner Liste vorhanden, den bestehenden „Projekt nicht
   gefunden“-Zustand anzeigen.

Falls eine ID wegen inkonsistenter oder zwischengespeicherter Daten zeitweise
in beiden Listen vorkommt, hat `disabled_projects` Vorrang.

Der Viewer darf für deaktivierte Projekte nicht auf `link`, `viewer_path` oder
`s3_path` zurückfallen. Diese Felder bleiben absichtlich im Index, damit der
Uploader das Projekt später wieder aktivieren kann.

## Cache-Verhalten

`projects_index.json` wird vom Uploader mit `Cache-Control: no-cache`
gespeichert. Der Viewer soll den Index beim Seitenaufruf revalidieren und nicht
allein aus einem langlebigen In-Memory-, Service-Worker- oder Browser-Cache
auflösen. Falls der bestehende Fetch-Pfad das nicht garantiert, für den Index
`cache: "no-store"` verwenden oder eine gleichwertige Revalidierung ergänzen.

Eine unmittelbar nach dem Deaktivieren neu geladene Direktlink-Seite muss den
deaktivierten Zustand erkennen.

## Tests

Mindestens folgende Fälle abdecken:

- ID nur in `projects`: Projekt wird geladen.
- ID nur in `disabled_projects`: Deaktiviert-Meldung erscheint.
- ID in beiden Listen: Projekt wird nicht geladen.
- ID in keiner Liste: „Nicht gefunden“ erscheint.
- Bei deaktivierter ID werden keine Punktwolken-Assets angefordert.
- Nach dem Verschieben von `projects` nach `disabled_projects` blockiert ein
  Reload desselben Direktlinks das Projekt ohne manuelles Cache-Leeren.
- Nach der Reaktivierung (`disabled_projects` nach `projects`) funktioniert
  derselbe Link wieder.

## Abnahmekriterien

- `?id=<deaktivierte-id>` zeigt keine Punktwolke mehr.
- Es erscheint eine verständliche Deaktiviert-Meldung.
- Im deaktivierten Zustand erfolgen keine Requests auf Projekt-/Punktwolken-
  Assets.
- Aktive Projekte und bestehende Viewer-Funktionen bleiben unverändert.
- Reaktivierte Projekte sind wieder über denselben Link erreichbar.

## Sicherheitsgrenze

Diese Änderung sperrt den Zugriff über den Dronautix Viewer. Sie ist keine
vollständige Zugriffskontrolle, solange S3-/CloudFront-Objekte öffentlich über
ihre direkten URLs erreichbar sind. Eine echte serverseitige Sperre erfordert
private Objekte und beispielsweise signierte CloudFront-URLs oder eine
Autorisierungsschicht; das ist ein separates Infrastruktur-Thema.
