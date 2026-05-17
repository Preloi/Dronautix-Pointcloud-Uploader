# AGENTS.md

## Projekt

Dieses Repository enthaelt den Dronautix Pointcloud Uploader. Die App ist eine Windows-Desktop-Anwendung auf Python/CustomTkinter-Basis. Sie laedt Punktwolkenprojekte nach S3 hoch, konvertiert LAS/LAZ-Dateien bei Bedarf mit PotreeConverter, unterstuetzt COPC-Direktupload und schreibt die Projekt-/Punktwolken-Metadaten fuer den Dronautix WebGL/Potree Viewer.

Wichtige Metadaten sind Projektname, Kunde, Viewer-/S3-Pfade, Punktwolkenformat sowie CRS-Informationen. Wenn vorhanden, werden horizontales CRS und vertikales Datum/vertikales CRS in `projects_index.json`, `metadata.json` und `cloud.js` geschrieben, damit der Viewer die Referenzsysteme erkennen kann.

Der Viewer liegt in einem separaten Repository und darf von diesem Projekt aus nicht geaendert werden.

## Release- und Update-Ablauf

Die App erkennt Updates beim Start ueber `latest-release.json` auf dem `master`-Branch des GitHub-Repositorys `Preloi/Dronautix-Pointcloud-Uploader`. Ein reiner Code-Push reicht nicht aus. Damit installierte User eine Update-Meldung bekommen, muss der Release-Ablauf vollstaendig abgeschlossen sein.

1. Versionsnummer in `app_version.py` erhoehen.
2. `python build_exe.py` ausfuehren. Das Skript synchronisiert `installer_version.iss`, `version_info.txt`, baut die EXE und den Installer, aktualisiert `latest-release.json` und schreibt den SHA-256-Hash des Installers.
3. Sicherstellen, dass `Output/Dronautix_Pointcloud_Uploader_Setup_<version>.exe` existiert.
4. `latest-release.json` pruefen: `version`, `installer_name`, `release_tag`, `installer_url` und `installer_sha256` muessen zur neuen Version und zum gebauten Installer passen.
5. Aenderungen committen und nach `dronautix/master` pushen. Der funktionierende Remote ist `dronautix`; `origin` zeigt auf ein altes/nicht verwendetes Repository.
6. Git-Tag `v<version>` erstellen und pushen.
7. GitHub Release `v<version>` im Repository `Preloi/Dronautix-Pointcloud-Uploader` erstellen und den Installer aus `Output/` als Asset hochladen.
8. Remote verifizieren:
   - `https://raw.githubusercontent.com/Preloi/Dronautix-Pointcloud-Uploader/master/latest-release.json` zeigt auf die neue Version.
   - Das GitHub-Release enthaelt den Installer als Asset.
   - Der Asset-SHA entspricht `installer_sha256`.

Erst wenn Manifest, Tag, Release und Installer-Asset zusammenpassen, zeigt eine alte installierte App beim Start eine neue Version an.

Wichtig: Das Manifest darf nicht auf eine Version zeigen, fuer die kein passendes GitHub-Release-Asset existiert. Sonst wird ein Update angeboten, das beim Download oder bei der Pruefung scheitert.

Seit Version 1.7.10 prueft der Updater keine Authenticode-Signatur mehr, sondern verlaesst sich auf die HTTPS-Download-URL und den SHA-256-Hash aus `latest-release.json`. Altversionen, die noch eine Authenticode-Pruefung enthalten, koennen unsignierte Installer nicht automatisch starten; in diesem Fall muss einmalig manuell auf 1.7.10 oder neuer installiert werden.

## Git-Hinweise

Vor dem Commit immer `git status --short --branch` pruefen. Nicht verwandte lokale Aenderungen nicht versehentlich mitcommiten.

Nach erfolgreichem Commit/Push den Remote-Stand mit `git ls-remote --heads dronautix master` pruefen. Fuer Releases zusaetzlich `git ls-remote --tags dronautix v<version>` und `gh release view v<version> --repo Preloi/Dronautix-Pointcloud-Uploader` verwenden.
