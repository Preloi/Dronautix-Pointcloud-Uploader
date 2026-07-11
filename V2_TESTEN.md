# V2 lokal testen

## Schnellstart

Aus dem Quellcode (Final-Modus wie in der installierten App):

```text
python Dronautix_Pointcloud_Uploader_v2_final.py
```

Preview-Modus (getrennte Config, fuer Entwicklung):

```text
V2_PREVIEW_STARTEN.bat
```

Final-V2-Kandidaten-Installer fuer Installer-Tests:

```text
Output_v2_final_candidate\Dronautix_Pointcloud_Uploader_Setup_2.0.exe
```

Der Installer nutzt die produktive AppId und kann eine bestehende Installation
ersetzen. Fuer funktionale Tests zuerst den Quellcode-Start verwenden.

## Stand

- Version: 2.0
- App-Struktur: Sidebar Upload / Projektverwaltung / Aktivitaeten / Einstellungen
- Update-System: Auto-Check beim Start (Kanal "Stable"), Download mit
  SHA-256-Pruefung und Installer-Start ueber "Update pruefen" bzw. Dialog
- Ohne S3-Verbindung: keine Beispieldaten mehr; App oeffnet die Einstellungen
- S3-Akzeptanzsmoke: 11/11 Szenarien bestanden
- Testsuite: 493 passed, 11 skipped (Golden-Capture-Platzhalter)

## Noch nicht fuer Release erledigt

- Legacy Golden Masters: 0/11
- V2-vs-Golden-Vergleich: 0/11
- GitHub Asset SHA: wartet auf Release-Asset v2.0
- Altversions-Update-Test: wartet auf veroeffentlichtes Release-Asset

## Wichtige Testfaelle

- Upload LAS/LAZ mit Konvertierung
- Upload COPC
- Multi-Cloud-Projekt
- Projektverwaltung: duplizieren, loeschen, umbenennen, Link deaktivieren
- Punktwolkendaten austauschen: einzelne Cloud und komplette Multi-Cloud-Liste
- Download eines Projekts
- Einstellungen: S3-Verbindungstest
- Update pruefen (Einstellungen) und Auto-Check beim Start
