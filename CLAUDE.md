# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Windows desktop application (Python/CustomTkinter) for uploading pointcloud data (LAS/LAZ/COPC) to AWS S3 and generating viewer metadata for the Dronautix WebGL/Potree Viewer. The viewer is a separate repository and must not be modified from here.

Current version is defined in `app_version.py`. S3 bucket: `potreedronautix` (eu-central-1). Viewer URL pattern: `https://pointcloud.dronautix.at/index.html?id=<short_id>`.

## Commands

### Run the application
```bash
python Dronautix_Pointcloud_Uploader.py
```

### Build installer (syncs versions, builds EXE via PyInstaller, creates Inno Setup installer)
```bash
python build_exe.py
```

There are no automated tests. No linter is configured.

## Architecture

### Monolithic core pattern
Nearly all business logic, UI rendering, and helpers live in `dronautix_uploader/main.py` (~10K lines). The other modules in `dronautix_uploader/` (`config.py`, `s3_operations.py`, `project_ops.py`, `crs.py`, `converter.py`, `utils.py`, `ui_helpers.py`) are **facade modules** that re-export functions from `main.py` via `__getattr__` delegation. They exist for cleaner import paths but contain no independent logic.

The `views/` subdirectory (`upload_view.py`, `projects_view.py`, `convert_view.py`, `settings_view.py`) follows the same facade pattern.

### Key function groups in main.py

- **Upload orchestration**: `run_process()` (single cloud, 5 steps), `run_multi_upload_process()` (multi cloud)
- **Project operations**: `replace_project_process()`, `duplicate_project_process()`, `download_project_data_process()`, `rename_project_metadata_process()`
- **S3 operations**: `create_s3_client()`, `upload_files_to_s3()`, `load_projects_index()`, `save_projects_index()`, `collect_project_objects()`, `delete_s3_objects()`
- **CRS handling**: `detect_pointcloud_crs()`, `resolve_pointcloud_crs()`, `read_las_projection_records()`, `extract_epsg_from_wkt()`, `write_potree_metadata_crs()`
- **Conversion**: `run_potree_conversion()` (runs bundled PotreeConverter.exe from `bundled_tools/PotreeConverter/`)
- **Update system**: `check_for_available_update()`, `load_update_manifest()`, `download_update_installer()` — checks `latest-release.json` on GitHub master, verifies SHA-256
- **UI**: `show_main_view()`, `show_upload_view()`, `show_projects_view()`, `show_convert_view()`, `show_settings_view()`

### Global UI state
Navigation and view state is managed via module-level globals: `nav_buttons`, `app_views`, `current_view_name`, `selected_upload_files`.

### Threading model
Long-running operations (upload, conversion, download) run in background threads. GUI updates from threads use `root.after()` for thread safety. Progress tracking via `UploadProgress` class.

## Data Flow

1. User selects LAS/LAZ/COPC files via dialog or drag-and-drop
2. For LAS/LAZ: PotreeConverter runs locally, producing Potree output
3. Files upload to S3 under `pointclouds/<customer_slug>/<project_id>/...`
4. `projects_index.json` is updated with project metadata (including `pointclouds[]` array for multi-cloud)
5. `cloud.js` metadata is written with CRS info
6. Viewer link is generated; local temp files are cleaned up

### Key metadata files on S3
- `projects_index.json` — master project list
- `deleted_projects.json` — soft-deleted projects (30-day retention)
- `cloud.js` — per-project viewer metadata with CRS

## Versioning and Releases

Version is centralized in `app_version.py`. The build script (`build_exe.py`) propagates it to `version_info.txt`, `installer_version.iss`, and `latest-release.json`.

Full release process is documented in `AGENTS.md`. Key points:
- Bump version in `app_version.py`, then run `build_exe.py`
- Push to `dronautix` remote (not `origin` — that's an old/unused remote)
- Create git tag `v<version>`, create GitHub Release with installer asset
- Manifest, tag, release, and asset SHA must all match

Since v1.7.10, the updater relies on HTTPS + SHA-256 only (no Authenticode check).

## Conventions

- German comments and UI text throughout the codebase
- snake_case for functions/variables, CamelCase for classes, UPPER_CASE for constants
- Credentials stored via Windows keyring (DPAPI), never plaintext in config
- User config lives at `%APPDATA%\DronautixUploader\config.json`

## Git Rules

- The active remote is `dronautix`, not `origin`
- Commit, push, and deploy only when explicitly asked by the user
- Always check `git status --short --branch` before committing
- Do not include unrelated local changes in commits
