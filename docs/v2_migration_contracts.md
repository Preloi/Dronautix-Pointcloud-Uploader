# V2 Migration Contracts

This document captures compatibility contracts that must remain true while the
CustomTkinter app is split into UI-free core services and a PySide6/QtWidgets
preview app.

## Golden Masters

Golden fixtures must be generated from the current production code before
moving behavior out of `dronautix_uploader/main.py`. The first fixture set must
cover:

- Single LAS/LAZ upload with Potree output.
- Single COPC direct upload.
- Multi-cloud upload with mixed Potree/COPC sources.
- Horizontal CRS plus vertical CRS/datum.
- Existing Potree output folder.
- Project management operations: duplicate, delete, rename, single replace,
  multi replace, disabled/link-state changes.

The required capture matrix is tracked in `tests/golden/manifest.json`. Real
legacy outputs belong under `tests/golden/captured/<scenario-id>/`; their
normalized snapshots belong under
`tests/golden/captured_normalized/<scenario-id>/`. The hand-written fixtures in
`tests/fixtures/golden_examples/` only verify normalization behavior and do not
replace legacy Golden Masters.

Each captured scenario must also include
`tests/golden/captured/<scenario-id>/provenance.json`. It records the legacy app
version/ref, capture timestamp, scenario inputs, captured files, and observed
contracts such as converter command, S3/index/delete order, CRS mismatch
behavior, disabled-link behavior, and cleanup/orphan behavior. A capture without
valid provenance is not cutover-ready.

Minimal provenance shape:

```json
{
  "scenario_id": "multi_replace",
  "legacy_app_version": "1.7.10",
  "legacy_git_ref": "dronautix/develop@<commit>",
  "captured_at_utc": "2026-06-21T00:00:00Z",
  "captured_files": ["projects_index.json", "metadata.json", "cloud.js"],
  "inputs": {
    "sources": [],
    "s3_prefix": "",
    "notes": ""
  },
  "observed_contracts": {
    "operation_order": [],
    "crs_behavior": "",
    "disabled_link_behavior": "",
    "cleanup_behavior": ""
  }
}
```

After capturing a scenario from the legacy app, normalize the captured files
with:

```text
python tools/normalize_golden_captures.py --scenario <scenario-id>
```

Running without `--scenario` normalizes all captured scenarios listed in the
manifest. A missing scenario directory remains pending; a scenario directory
that exists but lacks any required file must fail instead of producing a partial
reference snapshot.

Before and after capture, print the file-level checklist with:

```text
python tools/plan_golden_captures.py
```

Use `--scenario <scenario-id>` to inspect one scenario and `--strict` when a
non-zero exit code is needed until every raw and normalized legacy file is ready.

To start a real capture scenario, create the folders and provenance skeleton
first:

```text
python tools/init_golden_capture.py multi_replace --legacy-app-version 1.7.10 --legacy-git-ref dronautix/develop@<commit>
```

To prepare the complete 11-scenario capture matrix in one pass, use:

```text
python tools/init_golden_capture.py --all --legacy-app-version 1.7.10 --legacy-git-ref dronautix/develop@<commit>
```

Then run the legacy CustomTkinter app for that scenario and collect only the raw
legacy outputs in a staging directory. Import those required files into the
capture tree with:

Before any legacy run that can touch productive root metadata, create a local
backup of `projects_index.json` and `deleted_projects.json`:

```text
python tools/backup_legacy_s3_metadata.py backup
```

The backup command reads the configured S3 bucket and writes a timestamped
directory under `artifacts/`. If a manual restore is needed, use the generated
backup directory and pass the explicit confirmation flag:

```text
python tools/backup_legacy_s3_metadata.py restore <backup-dir> --confirm-restore-productive-metadata
```

```text
python tools/import_golden_capture.py multi_replace --source-dir <legacy-output-dir>
```

If the legacy run outputs are staged as `<source-root>/<scenario-id>/`, import
the complete matrix with:

```text
python tools/import_golden_capture.py --all --source-root <legacy-output-root>
```

Use `--dry-run` to validate the planned copy without writing files and `--force`
only when intentionally replacing already imported raw outputs. The importer
copies only the scenario's required files from the source directory root,
preflights them with the same normalization parser used by Golden comparison,
and refuses sources from generated Golden/V2 output roots. It does not write
`provenance.json` and does not normalize automatically.

After import, fill the `provenance.json` observations and run
`tools/normalize_golden_captures.py`. The init/import commands never generate
legacy `projects_index.json`, `metadata.json`, or `cloud.js`; those must come
from the legacy app.

The generated provenance template is scenario-aware for the riskiest cases.
`multi_replace` includes ordered source metadata, reused project/link state,
legacy delete-before-index-save order, key ledgers, CRS mismatch observations,
and legacy rollback observations. `disabled_link_state` includes active versus
disabled list membership, UI-only link flags, replace/rename membership
stability, and deleted-project entry fields. The template is only a capture
starting point: strict readiness rejects unfilled observation placeholders for
these scenarios.

Generate deterministic raw V2 staging output with the V2 core services,
ProjectManagementService, and Fake-S3:

```text
python tools/generate_v2_golden_output.py --scenario single_copc_upload
```

Running without `--scenario` generates every supported V2 scenario under
`artifacts/v2-golden-generated/`, including upload and project-management
scenarios. The generator writes only V2 raw outputs; it does not create or
replace legacy Golden Masters.

Import raw V2 files into
`tests/golden/v2_outputs/<scenario-id>/` with:

```text
python tools/import_v2_golden_output.py multi_replace --source-dir <v2-output-dir>
```

The V2 importer uses the same required-file manifest and normalization preflight
as the legacy importer, but refuses sources from `tests/golden/captured/`,
`tests/golden/captured_normalized/`, or `tests/golden/v2_outputs/` so V2
comparison data cannot be seeded from Golden references. Use `--dry-run` before
copying and `--force` only when intentionally replacing raw V2 outputs.

Then compare the imported V2 files to the normalized legacy snapshots with:

```text
python tools/compare_v2_to_golden.py --scenario <scenario-id> --strict
```

The comparison normalizes V2 `projects_index.json`, `metadata.json`, and
`cloud.js` exactly like the legacy capture before comparing.

The strict cutover CLI reports the V2 side separately before the legacy
comparison:

- `V2 output files` only verifies that every manifest-required V2 raw file is
  present and normalizable under `tests/golden/v2_outputs/<scenario-id>/` or a
  `--v2-output-root` override.
- `V2 output freshness` regenerates deterministic V2 raw outputs from the
  current code in a temporary directory and compares them to the imported V2
  outputs, so stale imported snapshots cannot pass cutover.
- `V2 outputs match Golden Masters` compares those V2 files against normalized
  legacy Golden Masters. A ready V2 output set does not substitute for missing
  legacy captures.

Golden comparisons must normalize JSON and `cloud.js` before comparing:

- Sort object keys.
- Preserve Unicode with stable UTF-8 output.
- Mask timestamps, generated IDs, release timestamps, `deleted_at`, and
  `disabled_at`.
- Round floats to a fixed precision.
- Parse `cloud.js = {...};` into JSON before canonicalization.

## Converter Contract

The PotreeConverter command is frozen from the legacy app:

```text
[converter_path, source_file, "-o", output_dir, "--overwrite"]
```

The process working directory is `os.path.dirname(converter_path)`. Standard
error is merged into standard output, lines are logged with `[POTREE]`, percent
matches drive progress, and non-zero exit codes raise an error.

## S3 Upload Contract

S3 uploads use `upload_file` with:

```python
ExtraArgs={
    "ContentType": mimetypes.guess_type(local_path)[0] or "application/octet-stream",
    "CacheControl": "no-cache, no-store, must-revalidate, max-age=0",
}
```

COPC direct uploads always target:

```text
{s3_prefix}/source.copc.laz
```

Potree uploads recursively preserve relative paths under the output directory
and sort `metadata.json` last. Upload workflows must keep a ledger of S3 keys
only after `upload_file` returns successfully. Rollback deletes exactly that
ledger while the project index has not been updated.

## CRS Contract

Pointcloud-level CRS is preserved independently for every cloud entry. A
project-level CRS is written only when all active pointclouds have an equivalent
CRS summary. If a multi-cloud replace produces mixed or missing CRS values, V2
must remove stale project-level CRS fields while keeping pointcloud-level CRS.

Project-level CRS fields to clear on mismatch:

- `crs`
- `projection`
- `epsg`
- `vertical_crs`
- `vertical_epsg`
- `vertical_projection`
- `vertical_datum`
- `crs_info`

## Project Management Contract

The V2 navigation label is **Projektverwaltung**. Required actions are:

- Duplizieren
- Löschen
- Umbenennen
- Punktwolkendaten austauschen
- Projekt herunterladen
- Link kopieren
- Im Browser öffnen

All actions must support single-cloud and multi-cloud projects. Multi-cloud
projects need a detail panel for individual pointclouds plus an operation to
replace the complete `pointclouds` list.

Replace workflows must reuse the same convert/upload/metadata pipeline as new
uploads. They must not implement a parallel converter or upload path.

The Qt project-management UI routes actions by stable action IDs, not button
text. Rename, duplicate, and delete use Qt dialog payloads that validate to
UI-free controller inputs before calling the service layer. Delete must require
an explicit confirmation dialog before the service call. Replace actions stay
behind validated source-path payloads so file selection stays in dialogs while
conversion, S3/viewer path construction, upload, index-save, and cleanup remain
in UI-free core services.

Download is a parity action for existing projects. It requires a project-level
`s3_path`, downloads every non-folder S3 object under that prefix, never writes
`projects_index.json` or `deleted_projects.json`, and reports progress through
`ProgressEvent`. The local target folder name must keep the legacy shape:

```text
{sanitize(kunde)}_{sanitize(projekt)}_{id}
```

Downloaded object paths must be normalized through the safe path builder so S3
keys cannot escape the selected target directory.

Download cancellation is caller-driven through a UI-free callback. Cancellation
is checked before each object and during transfer progress callbacks. A
cancelled download returns `DownloadResult(status="cancelled")` with the target
directory and the list of files that completed before cancellation; it does not
mutate S3 metadata or project indexes. If cancellation happens during an active
object transfer, the active partial local file is removed; previously completed
files remain in place and are reported in `downloaded_files`.

Link status changes move projects between `projects` and `disabled_projects`.
Disabling adds `disabled_at`, enabling removes `disabled_at`, and both
directions strip UI-only flags such as `_link_disabled` and `link_disabled`
before saving. S3 object data is not changed by link status operations.

Link copy/open are local Qt actions. Copy is allowed for disabled projects so a
user can still inspect the URL, while open is blocked for disabled projects.

## Upload Preparation Contract

Upload and replace inputs share one preparation pipeline:

- `*.copc.laz` is uploaded directly as COPC.
- Existing Potree folders are accepted when `metadata.json` or `cloud.js` is
  present.
- Raw `.las`/`.laz` sources are converted through the frozen PotreeConverter
  boundary before upload.
- Source names and slugs are derived once during preparation and then reused by
  upload and replace workflows.
- CRS metadata may be attached per original source path and must remain at
  pointcloud level unless all active clouds share the same CRS summary.
- Project-management replace can accept raw source paths and delegates them to
  the same preparation pipeline before calling the existing single/full replace
  operations.

The Qt upload wizard must validate project, customer, sources, converter,
output folder, overwrite, and optional CRS values into a UI-free
`NewProjectUploadWorkflowRequest`. The Qt layer may own file/folder selection,
but it must call the upload workflow controller/service for preparation,
conversion, S3 upload, index save, rollback, and progress events.

## Replace Failure Contract

Complete multi-replace order:

1. Convert/prepare new clouds.
2. Upload new files while recording successful keys.
3. Save the index with the new `pointclouds` list.
4. Delete old S3 keys that are no longer referenced.

Failure rules:

- Failure before index save: delete successfully uploaded new keys and leave the
  index unchanged.
- Failure after index save during old-key cleanup: keep the index on the new
  list, report orphaned keys as warnings, and do not roll the index back.
- Link-disabled state remains unchanged for replace and rename.
- Duplicate creates an active cloned project unless a later product decision
  explicitly changes that behavior.
- Delete removes the project from both active `projects` and
  `disabled_projects`.

## Update And Cutover Contract

Preview V2 builds must stay outside the existing `latest-release.json` update
channel. Final V2 must keep the existing app name, AppId, installer naming,
GitHub release path, and SHA-256 manifest verification. First final startup must
migrate or read the legacy `%APPDATA%\DronautixUploader\config.json` and the
legacy `DronautixUploader` keyring credentials.

V2 Preview uses `%APPDATA%\DronautixUploaderV2Preview\config.json` and writes
new credentials to the `DronautixUploaderV2Preview` keyring service. If both
runtime credentials are missing from config, it reads keyring credentials as a
pair: `DronautixUploaderV2Preview` first, then legacy `DronautixUploader`.
This keeps existing installations usable without clobbering their production
credentials or mixing Access Key and Secret from different keyring services.

The Qt preview update check is explicit and non-invasive:

- `Preview` and `Manuell` channels do not request `latest-release.json`.
- `Stable` loads and validates the manifest in a background worker.
- The preview check never downloads, launches, or installs an update.
- Manifest validation must reject wrong hosts, wrong release tags, unsafe
  installer names, missing SHA-256 values, and SHA mismatches.

Core installer downloads must be atomic: bytes are written to
`<installer>.download`, then moved into the final installer path only after the
download stream closes. If download fails, the temporary file is removed. If
SHA-256 verification fails, the final installer is removed and the result is a
failed update download.

V2 preview packaging uses `build_v2_preview.py`. That script builds
`Dronautix_Pointcloud_Uploader_v2.py` into `dist_v2_preview/` and intentionally
does not modify `latest-release.json`, `Output/`, the Inno Setup installer, or
the production update channel. The existing `build_exe.py` remains the legacy
release path until the final V2 cutover is explicitly approved.

Preview build dependencies are declared separately in
`requirements-v2-preview.txt`, which extends the legacy `requirements.txt` with
`PySide6`.

The fast local preview gate is `tools/check_v2_preview_ready.py`. It verifies
only items that can be proven without publishing or touching productive S3:

```text
python tools/check_v2_preview_ready.py
```

This gate checks the preview entrypoint, isolated preview build command,
required bundled files, import safety without installed PySide6, and current V2
fixture output presence/freshness. Missing build packages are warnings by
default so source-level preview readiness can be separated from EXE packaging.
Use the stricter build-packaging form when the local machine is expected to
produce a runnable preview executable:

```text
python tools/check_v2_preview_ready.py --require-build-deps
```

Legacy Golden Masters, real S3 acceptance, GitHub asset SHA verification and
installed legacy update testing are not preview gates; they remain mandatory
only for final production cutover.

Final-V2 packaging is prepared through an isolated candidate contract before
the production release path is changed. `tools/check_v2_final_packaging_contract.py`
validates that a future final build uses
`Dronautix_Pointcloud_Uploader_v2_final.py`, which calls the same Qt app in
explicit Final mode, while `Dronautix_Pointcloud_Uploader_v2.py` remains the
separate Preview entrypoint. The contract also preserves production app name,
exe name, AppId, installer naming, GitHub release URL shape and SHA-256
manifest field. The tool writes only to
`artifacts/v2-final-candidate-release.json` when explicitly called with
`--write`; it must not modify `latest-release.json`, `Output/`,
`installer_version.iss`, `version_info.txt`, `build_exe.py` or the production
PyInstaller spec.

`build_v2_final_candidate.py` is the isolated executable build path for that
contract. It uses the production PyInstaller name and app identity, but writes
only candidate artifacts such as `version_info_v2_final_candidate.txt`,
`installer_version_v2_final_candidate.iss`,
`Dronautix_Pointcloud_Uploader_v2_final_candidate.iss`,
`build_v2_final_candidate/`, `dist_v2_final_candidate/`,
`Output_v2_final_candidate/` and `artifacts/v2-final-candidate-release.json`.
If Inno Setup is available, it builds the installer only into
`Output_v2_final_candidate/` and writes that installer SHA into the candidate
manifest. It does not publish a release and does not update the production
manifest.

Dashboard cutover readiness must be based on an explicit checklist, not on
credentials alone. Required gates are:

- Runtime connected to real S3-backed controllers.
- Legacy Golden Masters captured and normalized.
- Preview packaging remains separate from the production update channel.
- Final V2 packaging preserves AppId, installer naming and manifest contract.
- Real S3 acceptance test passed.
- GitHub Release asset SHA verified against `installer_sha256`.
- Update from an installed legacy version tested.

The strict CLI gate is `tools/check_v2_cutover_ready.py`. It combines Golden
Master readiness, current V2 output freshness, the Final-V2 packaging candidate
contract, the persisted Final-V2 candidate manifest, and a local manual
acceptance evidence file at `artifacts/v2-cutover-acceptance.json`. Generate
the template with:

```text
python tools/check_v2_cutover_ready.py --write-template
```

The evidence file must mark the real S3 acceptance test, GitHub asset SHA check,
and update-from-legacy test as passed with concrete fields. The real S3 gate
must list every scenario from the Golden manifest under `scenarios_passed`; the
strict cutover gate blocks if any manifest scenario is missing. The gate must
remain blocked while any evidence is missing or malformed.

The S3 acceptance gate can be filled by the isolated smoke runner:

```text
python tools/run_v2_s3_acceptance_smoke.py --confirm-real-s3-writes --write-acceptance
```

The runner uses the preview runtime credentials by default, creates isolated
metadata objects below `v2-cutover-acceptance/<run>/`, and wraps the S3 client
with a write fence so uploads, copies, deletes and metadata writes are rejected
outside the acceptance project prefix and test metadata prefix. It does not
write productive `projects_index.json` or `deleted_projects.json`. The command
requires `--confirm-real-s3-writes` because it performs real S3 uploads and
cleanup deletes.

The GitHub asset SHA gate can be filled by the release asset verifier:

```text
python tools/check_v2_github_asset_sha.py --write-acceptance
```

By default the verifier reads `artifacts/v2-final-candidate-release.json`,
streams the GitHub release asset from the candidate `installer_url`, hashes the
bytes, and compares that SHA-256 to
`release_manifest_candidate.installer_sha256`. It blocks while the candidate
manifest still contains `PENDING_FINAL_INSTALLER_SHA256`, and it can be pointed
at a predownloaded asset with `--asset-path` for offline verification. The
strict cutover check remains offline and consumes only the persisted evidence.

After testing an installed legacy version against the final V2 update channel,
record the legacy-update gate with:

```text
python tools/record_v2_legacy_update_acceptance.py --from-version <installed-old-version> --installed-app-id-preserved --update-prompt-seen --download-sha-verified --post-update-launch-ok --legacy-config-or-keyring-available --write-acceptance
```

The command blocks if any required observation flag is missing or if the target
version is not newer than the installed legacy version. It only records local
evidence; it does not perform the update itself.

The evidence file uses `schema_version: 1`, candidate fields
(`candidate_version`, `candidate_manifest_path`, `candidate_installer_name`,
`candidate_installer_sha256`) and a nested `gates` object. Required gates are
`real_s3_acceptance`, `github_asset_sha`, and `legacy_installed_update`.
`github_asset_sha` must include matching `manifest_sha256` and `asset_sha256`
values and must match the Final-V2 packaging candidate contract and the
persisted `artifacts/v2-final-candidate-release.json`. Once the SHA gate is
marked passed, the candidate manifest's `release_manifest_candidate.installer_sha256`
must equal the verified GitHub asset SHA. The S3 gate must include a test
prefix, completed timestamp, passed scenarios, and explicit confirmation for
`projects_index`, metadata, and cleanup verification across the full manifest
scenario matrix. The legacy-update gate must confirm AppId preservation, update
prompt, SHA-verified download, post-update launch, and legacy config/keyring
continuity.
