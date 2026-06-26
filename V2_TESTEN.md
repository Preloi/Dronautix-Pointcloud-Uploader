# V2 lokal testen

## Schnellstart

Preview starten:

```text
V2_PREVIEW_STARTEN.bat
```

Direkte Preview-EXE:

```text
dist_v2_preview\Dronautix_Pointcloud_Uploader_v2_preview.exe
```

Final-V2-Kandidaten-Installer fuer spaetere Installer-Tests:

```text
Output_v2_final_candidate\Dronautix_Pointcloud_Uploader_Setup_2.0.exe
```

Der Installer nutzt die produktive AppId und kann eine bestehende Installation
ersetzen. Fuer funktionale Tests zuerst die Preview verwenden.

## Stand

- Version: 2.0
- Preview-Build: gebaut
- Final-V2-Kandidat: gebaut
- Final-V2-Kandidaten-Installer: gebaut
- S3-Akzeptanzsmoke: 11/11 Szenarien bestanden
- Testsuite: 484 passed, 11 skipped

## Noch nicht fuer Release erledigt

- Legacy Golden Masters: 0/11
- V2-vs-Golden-Vergleich: 0/11
- GitHub Asset SHA: wartet auf Release-Asset v2.0
- Altversions-Update-Test: wartet auf veroeffentlichtes Release-Asset

## Wichtige Testfaelle

- Upload LAS/LAZ mit Konvertierung
- Upload COPC
- Multi-Cloud-Projekt
- Projektverwaltung: duplizieren, loeschen, umbenennen
- Punktwolkendaten austauschen: einzelne Cloud und komplette Multi-Cloud-Liste
- Download eines Projekts
- Einstellungen: S3-Verbindungstest
