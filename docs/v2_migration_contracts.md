# V2 Contracts

Compatibility contracts of the PySide6/QtWidgets app (V2) with the web viewer,
S3 and installed versions. They must stay true for every change. The legacy
CustomTkinter app (V1) and the one-off V2 cutover tooling have been removed.

## Output Snapshots

`tests/snapshots/<scenario>/` holds the exact viewer files (`projects_index.json`,
`metadata.json`, `cloud.js`, `deleted_projects.json`) plus `side_effects.json`
(S3 calls with keys, content types, cache headers and order) for ten scenarios:
single/multi/vertical-CRS/existing-Potree uploads, duplicate, delete, rename,
single and multi replace, and disabled link state. `core/output_snapshots.py`
generates them deterministically against an in-memory S3 double;
`tests/test_output_snapshots.py` requires byte identity. An intended change is
applied with `python tools/update_output_snapshots.py --write` and reviewed in
the git diff.

## Converter Contract

The V2 app requests Brotli output explicitly:

```text
[converter_path, source_file, "-o", output_dir, "--overwrite", "--encoding", "BROTLI"]
```

The process working directory is `os.path.dirname(converter_path)`. Standard
error is merged into standard output, lines are logged with `[POTREE]`, percent
matches drive progress, and non-zero exit codes raise an error. A successful
conversion must produce a valid `metadata.json` whose `encoding` is `BROTLI`.

## S3 Upload Contract

S3 uploads use `upload_file` with:

```python
ExtraArgs={
    "ContentType": mimetypes.guess_type(local_path)[0] or "application/octet-stream",
    "CacheControl": "public, max-age=31536000, immutable",
}
```

Replacement uploads use a fresh `versions/<data_version>` prefix so immutable
objects are never overwritten. `projects_index.json` remains uncached.

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

- `*.copc.laz` is rejected; supported inputs are LAS/LAZ files and existing
  Potree folders.
- Existing Potree folders require complete Potree 2 output: `metadata.json`,
  `hierarchy.bin`, and `octree.bin`. Potree 1 folders with only `cloud.js` are
  rejected before upload.
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

## Update Contract

The final app keeps the production app name, AppId, installer naming, GitHub
release path and SHA-256 manifest verification. It reads
`%APPDATA%\DronautixUploader\config.json` and the `DronautixUploader` keyring.
Creating vs. publishing `latest-release.json` and the release order are defined
in `docs/RELEASE.md`; `tools/verify_release_manifest.py` checks a manifest
against the uploaded asset.

The preview (`Dronautix_Pointcloud_Uploader_v2.py`, built with
`build_v2_preview.py` into `dist_v2_preview/`) stays outside the update channel.
It uses `%APPDATA%\DronautixUploaderV2Preview\config.json`, writes credentials
to the `DronautixUploaderV2Preview` keyring service and reads keyring
credentials as a pair: preview first, then `DronautixUploader`, unless
"Zugangsdaten entfernen" disabled that fallback (`keyring_fallback: false`).

The update check and installer download:

- `Manuell` does not request `latest-release.json`; `Stable` checks at startup
  in a background worker; pre-releases are never offered.
- Manifest validation rejects wrong hosts, wrong release tags, unsafe installer
  names, missing SHA-256 values and SHA mismatches.
- Downloads go to a fresh folder, are written to `<installer>.download` and
  moved into place only after the stream closes; failed or mismatching
  downloads are removed. The SHA-256 is checked again, and cancellation
  honoured, right before the installer is started.
