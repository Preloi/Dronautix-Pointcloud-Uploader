# V2 Contracts

Compatibility contracts of the PySide6/QtWidgets app (V2) with the web viewer,
S3 and installed versions. They must stay true for every change. The legacy
CustomTkinter app (V1) and the one-off V2 cutover tooling have been removed.

## Output Snapshots

`tests/snapshots/<scenario>/` holds the exact viewer files (`projects_index.json`,
`metadata.json`, `cloud.js`, `deleted_projects.json`) plus `side_effects.json`
(S3 calls with keys, content types, cache headers and order) for fourteen
scenarios: single/multi/vertical-CRS/existing-Potree uploads, duplicate, delete,
rename, single and multi replace, disabled link state, and four scenarios seeded
with index schema 2 entries (`schema2_single_replace`, `schema2_multi_replace`,
`schema2_add_pointcloud`, `schema2_link_rename`). The uploads and the duplicate
create new projects and therefore write schema 2; the other legacy scenarios
start from unmarked entries and protect the legacy output. `core/output_snapshots.py`
generates them deterministically against an in-memory S3 double;
`tests/test_output_snapshots.py` requires byte identity. An intended change is
applied with `python tools/update_output_snapshots.py --write` and reviewed in
the git diff.

## Project Index Schema Contract

Each `projects_index.json` entry carries its own index schema. It describes only
the index representation and is independent of `model.json.schema_version`.

| `index_schema_version` | Meaning |
| --- | --- |
| absent | Legacy schema. Read and written as before; never migrated or marked. |
| `2` (JSON integer) | Compact schema 2. Kept by every later action. |
| anything else, including `"2"`, `2.0`, `true`, `null`, `1` | Unknown. Listing, preview and download keep working; every writing action on this entry (rename, link state, delete, duplicate as source, cloud/model upload, replace, remove, CRS repair) fails before its first side effect, also for direct operation calls with prepared data. |

Which entries get schema 2:

- New projects in both branches of `build_new_project_upload()` (single cloud;
  several clouds and/or GLB models). The marker is the first key.
- A duplicate is a new project and gets schema 2 whatever its source used. The
  source entry and its S3 objects are not changed.
- Rebuilding a single-cloud entry on replace keeps an existing marker and never
  adds one. The shared builders (`build_single_project_metadata()`,
  `build_multi_project_metadata()`) do not set it.

Shape: the viewer shapes stay unchanged. A single cloud without models is the
project entry itself (with `name`); several clouds or a new project with GLB
models use `format: "multi"`, `pointclouds[]` and `models[]`. Project fields
(`id`, `kunde`, `projekt`, `datum`, `link`, `format`, `viewer_path`, `s3_path`),
cloud fields (`name`, `format`, `viewer_path`, `s3_path`, `visible`, other
display parameters), model fields (`id`, `name`, `format: "glb"`, `viewer_path`
to `model.json`, `s3_path` to the model prefix), `pointcloud_count`, sizes,
`history`, `disabled_at` and cleanup state are kept as before. CRS is written as
`crs`, `crs_name`, `vertical_crs`, `vertical_name`, each only with a known
value. Unknown other fields are not removed.

Compaction (`core/project_index_schema.compact_project_entry`) is pure,
idempotent, works on a copy and only touches CRS duplicates of a schema-2 entry,
of each of its `pointclouds[]` and of each of its `models[]`:

- The aliases `projection`, `epsg` (same reference as `crs`), `vertical_epsg`,
  `vertical_projection` (same as `vertical_crs`) and `vertical_datum` (same text
  as `vertical_name`) are removed. A WKT is never treated as equal to its EPSG
  code; only identical text counts.
- `crs_info` is removed only as a whole and only if every key is in the fixed
  list below and proven; otherwise the whole block stays (documented exception
  of schema 2, not a separate marker). For the listed keys empty strings and
  `null` carry no value. Any other key keeps the block whatever its value
  (including `null` and `""`), as does any non-string or unresolvable value.

  | Keys in `crs_info` | Removable when |
  | --- | --- |
  | `value`, `projection`, `crs`, `epsg`, `horizontal` | same interpreted reference as `crs` |
  | `code`, `auth` | derived exactly from an `EPSG:<n>` `crs` (`<n>`, `EPSG`) |
  | `name`, `crs_name` | same name as `crs_name`; lifted into `crs_name` if that key is absent |
  | `vertical_crs`, `vertical_epsg`, `vertical_projection` | same interpreted reference as `vertical_crs` |
  | `vertical_name`, `vertical_datum` | same name as `vertical_name`; lifted if absent |
  | `wkt`, `vertical_wkt`, `source` | the identical value is proven by the dataset's metadata documents |

  Conflicting names are never resolved silently. In addition the flat fields
  must give the uploader's readers the same horizontal/vertical reference and
  names as the block did (readers prefer `crs_info` over flat fields).
- Proof (`CrsDetailEvidence`) binds a dataset `s3_path` to raw values stored in
  that dataset's `metadata.json`/`cloud.js` (`crs_info.wkt`,
  `crs_info.vertical_wkt`, `crs_info.source`, `srs.wkt`; the top-level Potree
  `source` is the input file name and never counts). A multi project's summary
  is proven by the first active cloud it is taken from. A GLB manifest proves
  nothing. Proof comes from: the staged metadata files actually uploaded (new
  upload, replace/add from sources); the documents copied into the new,
  unpublished prefix of a duplicate (unreadable copies only withhold proof; a
  user cancel, also one set while a response is read, stays a cancel and the
  copy is not published); the documents a CRS repair read or wrote. Prepared
  clouds handed in directly carry no proof, so their details stay. The output of
  `detect_crs_from_metadata_dict()` (normalized, `source: "auto"`) is never proof.

Save boundary: `ProjectMetadataRepository.save_projects_index(index, context)`
with an `IndexSaveContext(project_id, evidence)` compacts only the named target
in `projects` and `disabled_projects`, and only if it is schema 2. Without a
context nothing is compacted; all other entries keep their content and relative
order (only the existing UI-flag stripping applies). A missing target (delete)
is fine. The context lives only in the call; it holds no ETag. The compacted
copy is only the request body: the `IfMatch` condition always comes from the
loaded snapshot handed to save, after a rebase the fresh one. Conflict rebase,
uncertain-write handling and rollbacks are unchanged; rollbacks restore the
index in memory and add no index write.

The context is passed for new uploads, duplicates, rename, link state, delete
(both index saves), cloud replace/add/remove, GLB add/replace/remove and CRS
repair. Reading, download, foreign entries and rollback get none. Ordinary
actions do no extra S3 reads only for compaction; an already compact entry stays
compact, an entry re-inflated by an older uploader loses its safe duplicates.

Structure changes in schema 2: adding clouds to a single-cloud entry moves the
entry's raw `crs_info` unchanged to the original cloud (the normalized view
would drop unknown keys); the legacy flow keeps its behaviour. Before a
schema-2 multi project's summary is recomputed (add, remove, replace), every
non-reconstructible detail of the project `crs_info` (an unknown key also with
an empty value) must be held by one of its clouds; otherwise only that action is rejected before any upload, naming the
field. Explicitly removed or replaced clouds may lose their old metadata; it is
not moved to the replacement.

CRS repair keeps the candidate order and the resulting common CRS (project
index, then per cloud its index entry and documents). It backfills copies of the
target documents and keeps their WKT, source and unknown fields; no details are
moved between clouds and GLB names are unaffected. The Potree writers,
`model.json`, its hash and `data_version` keep their contracts.

Writer compatibility: version 2.2.2 introduces this contract. Use 2.2.2 for all
later writes to schema-2 projects; future versions must preserve this contract.
The unchanged 2.2.1 writer was checked against local compact fixtures: replacing
a single-cloud project removes the schema marker and reintroduces CRS aliases;
replacing all clouds in a multi project keeps the marker but reintroduces the
aliases. A missing marker is never inferred or restored automatically. Older
writers are therefore not a supported rollback for continued compact writes.
There is no migration of existing projects.

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

The viewer labels each cloud with its `name`. Multi-cloud entries carry it per
`pointclouds` item; a single-cloud entry is the cloud itself and carries a
top-level `name` (the source file stem or Potree folder name). Without it the
viewer falls back to "Kunde - Projekt".

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
