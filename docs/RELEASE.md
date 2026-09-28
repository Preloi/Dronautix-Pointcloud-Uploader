# Release-Ablauf

Installierte Apps lesen beim Start `latest-release.json` vom Branch `master`
des Repositorys `Preloi/Dronautix-Pointcloud-Uploader`. **Der Push dieses
Manifests nach `master` ist die Veröffentlichung**: Ab diesem Moment bietet
jede installierte App das Update an.

Deshalb gilt:

- **Erzeugen** – `python build_exe.py` schreibt `latest-release.json` lokal,
  nachdem der Installer gebaut ist. Das ist noch keine Veröffentlichung.
- **Veröffentlichen** – Das Manifest geht erst nach `master`, wenn
  1. die Checks *Tests* (Testsuite + EXE-Build mit Start-Selbsttest) auf dem
     Release-Commit grün sind,
  2. der Installer als Asset des GitHub-Releases `v<version>` hochgeladen ist,
  3. `python tools/verify_release_manifest.py --expect-app-version` für genau
     dieses Manifest erfolgreich ist (SHA-256 des hochgeladenen Assets ==
     `installer_sha256`).

Der Workflow *Release-Manifest* prüft Punkt 3 automatisch bei jedem Pull
Request und Push, der `latest-release.json` ändert. Ein Manifest, das auf ein
fehlendes oder abweichendes Asset zeigt, wird rot.

## Schritte

1. Version in `app_version.py` erhöhen, `python build_exe.py` einmal ausführen
   oder `sync_version_files()` nutzen, damit `version_info.txt` und
   `installer_version.iss` passen. Änderungen per Pull Request nach `master`;
   warten, bis *Tests* grün ist. `latest-release.json` dabei **nicht**
   mitändern.
2. Auf dem gemergten Release-Commit lokal `python build_exe.py` ausführen.
   Ergebnis: `Output/Dronautix_Pointcloud_Uploader_Setup_<version>.exe` und ein
   lokal erzeugtes `latest-release.json`.
3. Git-Tag `v<version>` auf den Release-Commit setzen und pushen.
4. GitHub-Release `v<version>` anlegen und den Installer aus `Output/`
   **unverändert** hochladen. Nicht umbenennen: Name und SHA-256 im Manifest
   beziehen sich auf genau diese Datei.
5. `python tools/verify_release_manifest.py --expect-app-version` lokal
   ausführen. Erst wenn das grün ist, weiter.
6. `latest-release.json` committen und nach `master` bringen (Pull Request).
   Der Workflow *Release-Manifest* prüft erneut; nach dem Merge ist das Update
   veröffentlicht.
7. Kontrolle: `https://raw.githubusercontent.com/Preloi/Dronautix-Pointcloud-Uploader/master/latest-release.json`
   zeigt die neue Version.

Ein Manifest darf nie auf eine Version zeigen, für die kein passendes
Release-Asset existiert; sonst scheitert das angebotene Update beim Download
oder bei der Prüfung.

## Schutz von `master` (derzeit nicht eingerichtet)

Solange nur eine Person am Code arbeitet, ist der Branch-Schutz bewusst nicht
aktiv. Die Checks laufen trotzdem bei jedem Push und Pull Request; die Regel
oben (erst grün, dann `master`, Manifest erst nach Asset-Prüfung) wird
eingehalten, aber nicht von GitHub erzwungen.

Nachrüsten, sobald mehrere Personen beitragen – *Settings → Branches →
Branch protection rules* für `master`:

- *Require a pull request before merging*
- *Require status checks to pass*: `Testsuite (Windows)`,
  `EXE-Build + Start-Selbsttest`, `Manifest gegen Release-Asset prüfen`
- *Do not allow bypassing the above settings*
