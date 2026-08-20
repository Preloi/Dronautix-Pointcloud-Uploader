# GLB-Toolchain (Windows x64)

Dieser Ordner ist absichtlich **nicht** produktionsbereit, solange
`toolchain-manifest.v1.json` nicht den Status `sealed` hat. Die App faellt dann
auf das unveraenderte, selbststaendige GLB zurueck und startet weder ein
globales `node`, `npm` noch ein Benutzerwerkzeug.

## Verbindliche Quellen

- Node.js 22.17.0, Windows x64: `https://nodejs.org/dist/v22.17.0/win-x64/node.exe`
  - SHA-256 `39d45b593f339d3ebdebd76474893dab5d7da1038920f65cf5bbcf0f20f3636`
  - Alternativarchiv: `node-v22.17.0-win-x64.zip`, SHA-256
    `721ab118a3aac8584348b132767eadf51379e0616f0db802cc1e66d7f0d98f85`.
- glTF-Transform CLI 4.4.2 und seine fest gelockten Abhaengigkeiten:
  `https://registry.npmjs.org/@gltf-transform/cli/-/cli-4.4.2.tgz`.
  Es liefert Meshoptimizer 1.0.1, Draco 1.5.7 und den offiziellen
  `gltf-validator` 2.0.0-dev.3.10 als Node-Abhaengigkeiten.
- Sharp 0.34.5 ist zusätzlich direkt gelockt, weil der KTX2-Runner PNG/JPEG/
  WebP/AVIF prüft und für das technisch erforderliche 1x1-zu-4x4-KTX-Fallback
  ausschließlich diese gebündelte Bibliothek verwendet.
- KTX-Software 4.4.2 fuer `ktx.exe`:
  `https://github.com/KhronosGroup/KTX-Software/releases/download/v4.4.2/KTX-Software-4.4.2-Windows-x64.exe`
  (6,417,024 Bytes, SHA-256
  `1f323b0fec19794f5e6c0425a61d4b1da396872a10be862d105f4f4b2d2957fe`).
  Der gebündelte Runner verwendet **`ktx create`** direkt mit UASTC-Qualität
  2 ohne RDO und festen sRGB-/linear-Formaten je Material-Slot. Ein separates
  `basisu.exe` wäre dafür kein kompatibler Ersatz. KTX erzeugt die KTX2-
  Texturen mit dem Basis-Universal-Bitstream.

## Offline-Versiegelung

1. Die Quellen auf einem vertrauenswuerdigen Windows-x64-Buildsystem
   herunterladen und nach `runtime/`, `node_modules/` und `ktx/bin/` legen.
   Keine globale Node- oder npm-Installation in den Release aufnehmen.
2. Vor dem Seal muessen die in `toolchain-manifest.v1.json` benannten
   Einstiegspfade existieren. Das lockfile und **alle** installierten
   Abhaengigkeitsdateien bleiben in `node_modules/` im Bundle.
3. `python tools/seal_glb_toolchain.py --seal` aus dem Repository-Root
   ausfuehren. Das erzeugt fuer jede Datei den SHA-256-Eintrag in
   `toolchain-integrity.v1.json`, setzt die Entry-Hashes und markiert das
   Manifest als `sealed`.
4. `python tools/seal_glb_toolchain.py --verify` ausfuehren. Erst nach dem
   lokalen Node-/Runner-Selbsttest darf ein Installer gebaut werden.

Der Sealer laedt nichts nach. Ohne die obigen Downloads ist ein versiegeltes
Bundle bewusst nicht erzeugbar; das verhindert, dass ein Release eine nur
behauptete Optimierungs- oder Decoderfaehigkeit ausliefert.
