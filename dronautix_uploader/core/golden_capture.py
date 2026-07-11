"""Golden Master capture helpers for legacy output fixtures."""

from __future__ import annotations

import json
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .golden_normalization import canonical_cloudjs_text, canonical_json_text


REQUIRED_PROVENANCE_STRING_FIELDS = (
    "scenario_id",
    "legacy_app_version",
    "legacy_git_ref",
    "captured_at_utc",
)
REQUIRED_PROVENANCE_OBJECT_FIELDS = (
    "inputs",
    "observed_contracts",
)


@dataclass(frozen=True)
class GoldenCaptureFile:
    raw_path: Path
    normalized_path: Path


@dataclass(frozen=True)
class GoldenCaptureScenarioResult:
    scenario_id: str
    status: str
    files: tuple[GoldenCaptureFile, ...] = ()
    message: str = ""


@dataclass(frozen=True)
class GoldenCaptureInitializationResult:
    scenario_id: str
    captured_dir: Path
    normalized_dir: Path
    provenance_path: Path | None
    written: bool


@dataclass(frozen=True)
class GoldenImportedCaptureFile:
    source_path: Path
    captured_path: Path


@dataclass(frozen=True)
class GoldenCaptureImportResult:
    scenario_id: str
    source_dir: Path
    captured_dir: Path
    copied_files: tuple[GoldenImportedCaptureFile, ...] = ()
    dry_run: bool = False


@dataclass(frozen=True)
class GoldenImportedV2OutputFile:
    source_path: Path
    output_path: Path


@dataclass(frozen=True)
class GoldenV2OutputImportResult:
    scenario_id: str
    source_dir: Path
    output_dir: Path
    copied_files: tuple[GoldenImportedV2OutputFile, ...] = ()
    dry_run: bool = False


@dataclass(frozen=True)
class GoldenReadinessIssue:
    scenario_id: str
    message: str


@dataclass(frozen=True)
class GoldenReadinessReport:
    ready: bool
    scenario_count: int
    captured_count: int
    issues: tuple[GoldenReadinessIssue, ...] = ()


@dataclass(frozen=True)
class GoldenV2OutputReadinessReport:
    ready: bool
    scenario_count: int
    output_count: int
    issues: tuple[GoldenReadinessIssue, ...] = ()


@dataclass(frozen=True)
class GoldenV2OutputFreshnessReport:
    ready: bool
    scenario_count: int
    checked_count: int
    issues: tuple[GoldenReadinessIssue, ...] = ()


@dataclass(frozen=True)
class GoldenCaptureScenarioPlan:
    scenario_id: str
    description: str
    captured_dir: Path
    normalized_dir: Path
    required_files: tuple[str, ...]
    missing_raw_files: tuple[str, ...] = ()
    missing_normalized_files: tuple[str, ...] = ()
    drifted_files: tuple[str, ...] = ()
    provenance_file: str = ""
    missing_provenance: bool = False
    provenance_issues: tuple[str, ...] = ()

    @property
    def status(self) -> str:
        if not self.captured_dir.exists():
            return "pending"
        if self.missing_raw_files:
            return "incomplete"
        if self.missing_normalized_files:
            return "needs_normalization"
        if self.drifted_files:
            return "drift"
        if self.missing_provenance:
            return "missing_provenance"
        if self.provenance_issues:
            return "invalid_provenance"
        return "ready"

    @property
    def ready(self) -> bool:
        return self.status == "ready"


@dataclass(frozen=True)
class GoldenCapturePlan:
    scenarios: tuple[GoldenCaptureScenarioPlan, ...]

    @property
    def ready_count(self) -> int:
        return sum(1 for scenario in self.scenarios if scenario.ready)

    @property
    def scenario_count(self) -> int:
        return len(self.scenarios)

    @property
    def ready(self) -> bool:
        return bool(self.scenarios) and self.ready_count == self.scenario_count


@dataclass(frozen=True)
class GoldenComparisonFileResult:
    file_name: str
    actual_path: Path
    expected_path: Path
    status: str
    detail: str = ""

    @property
    def match(self) -> bool:
        return self.status == "match"


@dataclass(frozen=True)
class GoldenScenarioComparisonResult:
    scenario_id: str
    actual_dir: Path
    expected_dir: Path
    files: tuple[GoldenComparisonFileResult, ...]

    @property
    def ready(self) -> bool:
        return bool(self.files) and all(file_result.match for file_result in self.files)


@dataclass(frozen=True)
class GoldenComparisonReport:
    scenarios: tuple[GoldenScenarioComparisonResult, ...]

    @property
    def ready_count(self) -> int:
        return sum(1 for scenario in self.scenarios if scenario.ready)

    @property
    def scenario_count(self) -> int:
        return len(self.scenarios)

    @property
    def ready(self) -> bool:
        return bool(self.scenarios) and self.ready_count == self.scenario_count


@dataclass(frozen=True)
class _GoldenScenarioFileCopy:
    source_path: Path
    target_path: Path


@dataclass(frozen=True)
class _GoldenScenarioFileImportPlan:
    scenario_id: str
    source_dir: Path
    target_dir: Path
    files: tuple[_GoldenScenarioFileCopy, ...]


def load_golden_manifest(manifest_path: str | Path) -> dict[str, Any]:
    path = Path(manifest_path)
    return json.loads(path.read_text(encoding="utf-8"))


def golden_manifest_scenario_ids(manifest_path: str | Path) -> tuple[str, ...]:
    """Return ordered scenario IDs from the Golden manifest."""

    manifest = load_golden_manifest(manifest_path)
    scenario_ids: list[str] = []
    for scenario in _selected_scenarios(manifest, None):
        scenario_id = str(scenario.get("id", "") or "").strip()
        if not scenario_id:
            raise ValueError("Golden scenario is missing an id.")
        if Path(scenario_id).name != scenario_id:
            raise ValueError(f"Golden scenario uses an unsafe id: {scenario_id}")
        scenario_ids.append(scenario_id)
    return tuple(scenario_ids)


def normalize_captured_golden_files(
    manifest_path: str | Path,
    *,
    scenario_id: str | None = None,
) -> tuple[GoldenCaptureScenarioResult, ...]:
    """Normalize captured legacy files into the snapshot directory.

    Missing capture directories stay pending; missing required files fail fast so
    an incomplete capture cannot silently become a reference snapshot.
    """

    manifest_path = Path(manifest_path)
    manifest = load_golden_manifest(manifest_path)
    scenarios = _selected_scenarios(manifest, scenario_id)
    captured_root = _resolve_manifest_path(manifest_path, manifest["captured_root"])
    normalized_root = _resolve_manifest_path(manifest_path, manifest["normalized_root"])
    return tuple(
        _normalize_scenario(scenario, captured_root=captured_root, normalized_root=normalized_root)
        for scenario in scenarios
    )


def check_golden_capture_readiness(manifest_path: str | Path) -> GoldenReadinessReport:
    """Return a strict Cutover report for all legacy Golden captures."""

    manifest_path = Path(manifest_path)
    manifest = load_golden_manifest(manifest_path)
    scenarios = _selected_scenarios(manifest, None)
    captured_root = _resolve_manifest_path(manifest_path, manifest["captured_root"])
    normalized_root = _resolve_manifest_path(manifest_path, manifest["normalized_root"])
    provenance_file = _provenance_file_name(manifest)
    issues: list[GoldenReadinessIssue] = []
    captured_count = 0

    for scenario in scenarios:
        scenario_id = str(scenario.get("id", "")).strip()
        required_files = tuple(str(file_name) for file_name in scenario.get("required_files", ()) if str(file_name))
        captured_dir = captured_root / scenario_id
        normalized_dir = normalized_root / scenario_id
        if not captured_dir.exists():
            issues.append(GoldenReadinessIssue(scenario_id, "Legacy capture fehlt."))
            continue
        raw_files_complete = True
        for file_name in required_files:
            if Path(file_name).name != file_name:
                issues.append(GoldenReadinessIssue(scenario_id, f"Unsicherer Dateiname: {file_name}"))
                raw_files_complete = False
                continue
            raw_path = captured_dir / file_name
            normalized_path = normalized_dir / normalized_capture_file_name(file_name)
            if not raw_path.is_file():
                issues.append(GoldenReadinessIssue(scenario_id, f"Captured File fehlt: {raw_path}"))
                raw_files_complete = False
                continue
            if not normalized_path.is_file():
                issues.append(GoldenReadinessIssue(scenario_id, f"Normalized Golden fehlt: {normalized_path}"))
                continue
            actual = normalize_capture_text(file_name, raw_path.read_text(encoding="utf-8"))
            expected = normalized_path.read_text(encoding="utf-8")
            if actual != expected:
                issues.append(GoldenReadinessIssue(scenario_id, f"Normalized Golden driftet: {file_name}"))
        if raw_files_complete:
            captured_count += 1
        if provenance_file:
            provenance_path = captured_dir / provenance_file
            provenance_issues = validate_golden_provenance_file(
                provenance_path,
                scenario_id=scenario_id,
                expected_captured_files=required_files,
                require_filled_observations=True,
            )
            for provenance_issue in provenance_issues:
                issues.append(GoldenReadinessIssue(scenario_id, provenance_issue))

    return GoldenReadinessReport(
        ready=not issues,
        scenario_count=len(scenarios),
        captured_count=captured_count,
        issues=tuple(issues),
    )


def check_v2_output_readiness(
    manifest_path: str | Path,
    *,
    actual_root: str | Path | None = None,
) -> GoldenV2OutputReadinessReport:
    """Return a strict report for imported/generated V2 raw output files.

    This does not compare against legacy Golden Masters. It only proves that the
    V2 side of the comparison is ready and parseable for every manifest file.
    """

    manifest_path = Path(manifest_path)
    manifest = load_golden_manifest(manifest_path)
    scenarios = _selected_scenarios(manifest, None)
    output_root = Path(actual_root) if actual_root is not None else _resolve_manifest_path(
        manifest_path,
        str(manifest.get("v2_output_root", "tests/golden/v2_outputs")),
    )
    issues: list[GoldenReadinessIssue] = []
    output_count = 0

    for scenario in scenarios:
        scenario_id = str(scenario.get("id", "")).strip()
        required_files = tuple(str(file_name) for file_name in scenario.get("required_files", ()) if str(file_name))
        output_dir = output_root / scenario_id
        if not output_dir.exists():
            issues.append(GoldenReadinessIssue(scenario_id, f"V2-Ausgabe fehlt: {output_dir}"))
            continue
        output_count += 1
        for file_name in required_files:
            if Path(file_name).name != file_name:
                issues.append(GoldenReadinessIssue(scenario_id, f"Unsicherer Dateiname: {file_name}"))
                continue
            output_path = output_dir / file_name
            if not output_path.is_file():
                issues.append(GoldenReadinessIssue(scenario_id, f"V2-Datei fehlt: {output_path}"))
                continue
            try:
                normalize_capture_text(file_name, output_path.read_text(encoding="utf-8"))
            except Exception as exc:
                issues.append(
                    GoldenReadinessIssue(
                        scenario_id,
                        f"V2-Datei ist nicht normalisierbar: {output_path} ({exc})",
                    )
                )

    return GoldenV2OutputReadinessReport(
        ready=not issues,
        scenario_count=len(scenarios),
        output_count=output_count,
        issues=tuple(issues),
    )


def check_v2_output_freshness(
    manifest_path: str | Path,
    *,
    actual_root: str | Path | None = None,
) -> GoldenV2OutputFreshnessReport:
    """Compare imported V2 raw outputs with freshly generated V2 outputs."""

    manifest_path = Path(manifest_path)
    manifest = load_golden_manifest(manifest_path)
    scenarios = _selected_scenarios(manifest, None)
    actual_base = Path(actual_root) if actual_root is not None else _resolve_manifest_path(
        manifest_path,
        str(manifest.get("v2_output_root", "tests/golden/v2_outputs")),
    )
    issues: list[GoldenReadinessIssue] = []
    checked_count = 0

    try:
        with tempfile.TemporaryDirectory(prefix="v2-golden-current-") as temp_dir:
            from .v2_golden_output import generate_v2_golden_outputs

            generate_v2_golden_outputs(manifest_path, output_root=temp_dir, overwrite=True)
            generated_root = Path(temp_dir)
            for scenario in scenarios:
                scenario_id = str(scenario.get("id", "")).strip()
                required_files = tuple(
                    str(file_name) for file_name in scenario.get("required_files", ()) if str(file_name)
                )
                generated_dir = generated_root / scenario_id
                actual_dir = actual_base / scenario_id
                if not generated_dir.exists():
                    issues.append(
                        GoldenReadinessIssue(
                            scenario_id,
                            f"Aktuelle V2-Generator-Ausgabe fehlt: {generated_dir}",
                        )
                    )
                    continue
                checked_count += 1
                for file_name in required_files:
                    if Path(file_name).name != file_name:
                        issues.append(GoldenReadinessIssue(scenario_id, f"Unsicherer Dateiname: {file_name}"))
                        continue
                    generated_path = generated_dir / file_name
                    actual_path = actual_dir / file_name
                    if not generated_path.is_file():
                        issues.append(
                            GoldenReadinessIssue(
                                scenario_id,
                                f"Aktuelle V2-Generator-Datei fehlt: {generated_path}",
                            )
                        )
                        continue
                    if not actual_path.is_file():
                        issues.append(GoldenReadinessIssue(scenario_id, f"Importierte V2-Datei fehlt: {actual_path}"))
                        continue
                    try:
                        generated_text = normalize_capture_text(file_name, generated_path.read_text(encoding="utf-8"))
                        actual_text = normalize_capture_text(file_name, actual_path.read_text(encoding="utf-8"))
                    except Exception as exc:
                        issues.append(
                            GoldenReadinessIssue(
                                scenario_id,
                                f"V2-Datei ist nicht normalisierbar: {file_name} ({exc})",
                            )
                        )
                        continue
                    if actual_text != generated_text:
                        issues.append(
                            GoldenReadinessIssue(
                                scenario_id,
                                f"Importierte V2-Datei ist veraltet: {actual_path}",
                            )
                        )
    except Exception as exc:
        issues.append(GoldenReadinessIssue("_generator", f"Aktueller V2-Golden-Generator fehlgeschlagen: {exc}"))

    return GoldenV2OutputFreshnessReport(
        ready=not issues,
        scenario_count=len(scenarios),
        checked_count=checked_count,
        issues=tuple(issues),
    )


def build_golden_capture_plan(
    manifest_path: str | Path,
    *,
    scenario_id: str | None = None,
) -> GoldenCapturePlan:
    """Return a file-level capture plan for real legacy Golden outputs."""

    manifest_path = Path(manifest_path)
    manifest = load_golden_manifest(manifest_path)
    scenarios = _selected_scenarios(manifest, scenario_id)
    captured_root = _resolve_manifest_path(manifest_path, manifest["captured_root"])
    normalized_root = _resolve_manifest_path(manifest_path, manifest["normalized_root"])
    provenance_file = _provenance_file_name(manifest)
    return GoldenCapturePlan(
        scenarios=tuple(
            _build_scenario_plan(
                scenario,
                captured_root=captured_root,
                normalized_root=normalized_root,
                provenance_file=provenance_file,
            )
            for scenario in scenarios
        )
    )


def compare_golden_outputs(
    manifest_path: str | Path,
    *,
    actual_root: str | Path | None = None,
    scenario_id: str | None = None,
) -> GoldenComparisonReport:
    """Compare V2 output files with normalized legacy Golden snapshots."""

    manifest_path = Path(manifest_path)
    manifest = load_golden_manifest(manifest_path)
    scenarios = _selected_scenarios(manifest, scenario_id)
    expected_root = _resolve_manifest_path(manifest_path, manifest["normalized_root"])
    actual_base = Path(actual_root) if actual_root is not None else _resolve_manifest_path(
        manifest_path,
        str(manifest.get("v2_output_root", "tests/golden/v2_outputs")),
    )
    return GoldenComparisonReport(
        scenarios=tuple(
            _compare_scenario_outputs(
                scenario,
                actual_root=actual_base,
                expected_root=expected_root,
            )
            for scenario in scenarios
        )
    )


def build_golden_provenance_template(
    manifest_path: str | Path,
    *,
    scenario_id: str,
    legacy_app_version: str = "",
    legacy_git_ref: str = "",
    captured_at_utc: str = "",
) -> dict[str, Any]:
    """Return a provenance skeleton for one legacy Golden capture scenario."""

    manifest = load_golden_manifest(manifest_path)
    scenario = _selected_scenarios(manifest, scenario_id)[0]
    required_files = tuple(str(file_name) for file_name in scenario.get("required_files", ()) if str(file_name))
    inputs, observed_contracts = _provenance_template_sections(scenario_id)
    return {
        "schema_version": 1,
        "scenario_id": scenario_id,
        "description": str(scenario.get("description", "") or ""),
        "legacy_app_version": legacy_app_version,
        "legacy_git_ref": legacy_git_ref,
        "captured_at_utc": captured_at_utc,
        "captured_files": list(required_files),
        "inputs": inputs,
        "observed_contracts": observed_contracts,
    }


def initialize_golden_capture_scenario(
    manifest_path: str | Path,
    *,
    scenario_id: str,
    legacy_app_version: str = "",
    legacy_git_ref: str = "",
    captured_at_utc: str = "",
    overwrite_provenance: bool = False,
) -> GoldenCaptureInitializationResult:
    """Create capture directories and a provenance template for one scenario."""

    manifest_path = Path(manifest_path)
    manifest = load_golden_manifest(manifest_path)
    scenario = _selected_scenarios(manifest, scenario_id)[0]
    captured_root = _resolve_manifest_path(manifest_path, manifest["captured_root"])
    normalized_root = _resolve_manifest_path(manifest_path, manifest["normalized_root"])
    provenance_file = _provenance_file_name(manifest)
    captured_dir = captured_root / scenario_id
    normalized_dir = normalized_root / scenario_id
    captured_dir.mkdir(parents=True, exist_ok=True)
    normalized_dir.mkdir(parents=True, exist_ok=True)

    provenance_path = captured_dir / provenance_file if provenance_file else None
    written = False
    if provenance_path is not None:
        if provenance_path.exists() and not overwrite_provenance:
            written = False
        else:
            template = build_golden_provenance_template(
                manifest_path,
                scenario_id=scenario_id,
                legacy_app_version=legacy_app_version,
                legacy_git_ref=legacy_git_ref,
                captured_at_utc=captured_at_utc,
            )
            provenance_path.write_text(
                json.dumps(template, indent=2, ensure_ascii=False) + "\n",
                encoding="utf-8",
                newline="\n",
            )
            written = True

    return GoldenCaptureInitializationResult(
        scenario_id=str(scenario.get("id", "") or scenario_id),
        captured_dir=captured_dir,
        normalized_dir=normalized_dir,
        provenance_path=provenance_path,
        written=written,
    )


def import_golden_capture_files(
    manifest_path: str | Path,
    *,
    scenario_id: str,
    source_dir: str | Path,
    overwrite: bool = False,
    dry_run: bool = False,
) -> GoldenCaptureImportResult:
    """Copy raw legacy output files for one scenario into the capture tree."""

    manifest_path = Path(manifest_path)
    manifest = load_golden_manifest(manifest_path)
    captured_root = _resolve_manifest_path(manifest_path, manifest["captured_root"])
    normalized_root = _resolve_manifest_path(manifest_path, manifest["normalized_root"])
    v2_output_root = _resolve_manifest_path(
        manifest_path,
        str(manifest.get("v2_output_root", "tests/golden/v2_outputs")),
    )
    plan = _prepare_scenario_file_import(
        manifest=manifest,
        scenario_id=scenario_id,
        source_dir=source_dir,
        target_root=captured_root,
        blocked_roots=(captured_root, normalized_root, v2_output_root, Path("artifacts/v2-golden-generated")),
        overwrite=overwrite,
        source_label="Golden capture",
    )
    mapped_files = tuple(
        GoldenImportedCaptureFile(source_path=file_result.source_path, captured_path=file_result.target_path)
        for file_result in plan.files
    )

    if dry_run:
        return GoldenCaptureImportResult(
            scenario_id=plan.scenario_id,
            source_dir=plan.source_dir,
            captured_dir=plan.target_dir,
            copied_files=mapped_files,
            dry_run=True,
        )

    _copy_scenario_files(plan)

    return GoldenCaptureImportResult(
        scenario_id=plan.scenario_id,
        source_dir=plan.source_dir,
        captured_dir=plan.target_dir,
        copied_files=mapped_files,
        dry_run=False,
    )


def import_v2_golden_output_files(
    manifest_path: str | Path,
    *,
    scenario_id: str,
    source_dir: str | Path,
    overwrite: bool = False,
    dry_run: bool = False,
) -> GoldenV2OutputImportResult:
    """Copy raw V2 output files for one scenario into the V2 Golden output tree."""

    manifest_path = Path(manifest_path)
    manifest = load_golden_manifest(manifest_path)
    captured_root = _resolve_manifest_path(manifest_path, manifest["captured_root"])
    normalized_root = _resolve_manifest_path(manifest_path, manifest["normalized_root"])
    v2_output_root = _resolve_manifest_path(
        manifest_path,
        str(manifest.get("v2_output_root", "tests/golden/v2_outputs")),
    )
    plan = _prepare_scenario_file_import(
        manifest=manifest,
        scenario_id=scenario_id,
        source_dir=source_dir,
        target_root=v2_output_root,
        blocked_roots=(captured_root, normalized_root, v2_output_root),
        overwrite=overwrite,
        source_label="V2 output",
    )
    mapped_files = tuple(
        GoldenImportedV2OutputFile(source_path=file_result.source_path, output_path=file_result.target_path)
        for file_result in plan.files
    )

    if dry_run:
        return GoldenV2OutputImportResult(
            scenario_id=plan.scenario_id,
            source_dir=plan.source_dir,
            output_dir=plan.target_dir,
            copied_files=mapped_files,
            dry_run=True,
        )

    _copy_scenario_files(plan)
    return GoldenV2OutputImportResult(
        scenario_id=plan.scenario_id,
        source_dir=plan.source_dir,
        output_dir=plan.target_dir,
        copied_files=mapped_files,
        dry_run=False,
    )


def normalize_capture_text(file_name: str, text: str) -> str:
    if file_name == "cloud.js":
        return canonical_cloudjs_text(text)
    return canonical_json_text(text)


def normalized_capture_file_name(file_name: str) -> str:
    if file_name == "cloud.js":
        return "cloud.normalized.json"
    return f"{Path(file_name).stem}.normalized.json"


def validate_golden_provenance_file(
    path: str | Path,
    *,
    scenario_id: str,
    expected_captured_files: tuple[str, ...] | list[str] = (),
    require_filled_observations: bool = False,
) -> tuple[str, ...]:
    provenance_path = Path(path)
    if not provenance_path.is_file():
        return (f"Provenienz fehlt: {provenance_path}",)
    try:
        data = json.loads(provenance_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return (f"Provenienz ist kein gueltiges JSON: {exc}",)
    return validate_golden_provenance(
        data,
        scenario_id=scenario_id,
        expected_captured_files=expected_captured_files,
        require_filled_observations=require_filled_observations,
    )


def validate_golden_provenance(
    data: Any,
    *,
    scenario_id: str,
    expected_captured_files: tuple[str, ...] | list[str] = (),
    require_filled_observations: bool = False,
) -> tuple[str, ...]:
    if not isinstance(data, dict):
        return ("Provenienz muss ein JSON-Objekt sein.",)

    issues: list[str] = []
    actual_scenario = str(data.get("scenario_id", "") or "").strip()
    if actual_scenario != scenario_id:
        issues.append("Provenienz scenario_id passt nicht zum Golden-Szenario.")
    for field in REQUIRED_PROVENANCE_STRING_FIELDS:
        if not str(data.get(field, "") or "").strip():
            issues.append(f"Provenienz Pflichtfeld fehlt: {field}.")
    for field in REQUIRED_PROVENANCE_OBJECT_FIELDS:
        value = data.get(field)
        if not isinstance(value, dict) or not value:
            issues.append(f"Provenienz Objekt fehlt oder ist leer: {field}.")
    captured_files = data.get("captured_files")
    if not isinstance(captured_files, list) or not all(str(value or "").strip() for value in captured_files):
        issues.append("Provenienz captured_files muss eine gefuellte Liste sein.")
    else:
        expected_files = tuple(str(value or "").strip() for value in expected_captured_files if str(value or "").strip())
        if expected_files and tuple(str(value or "").strip() for value in captured_files) != expected_files:
            issues.append("Provenienz captured_files passt nicht zur Golden-Manifest-Dateiliste.")
    if require_filled_observations:
        issues.extend(_validate_filled_provenance_observations(data, scenario_id=scenario_id))
    return tuple(issues)


def _validate_filled_provenance_observations(data: dict[str, Any], *, scenario_id: str) -> tuple[str, ...]:
    required_paths: tuple[tuple[str, ...], ...] = ()
    if scenario_id == "multi_replace":
        required_paths = (
            ("inputs", "project_id"),
            ("inputs", "original_link"),
            ("inputs", "original_disabled_state"),
            ("inputs", "sources"),
            ("observed_contracts", "converter_command"),
            ("observed_contracts", "converter_working_directory"),
            ("observed_contracts", "s3_extra_args"),
            ("observed_contracts", "index_update_order"),
            ("observed_contracts", "crs_behavior"),
            ("observed_contracts", "cleanup_behavior"),
            ("observed_contracts", "rollback_behavior"),
            ("observed_contracts", "common_crs_decision"),
            ("observed_contracts", "stale_project_crs_on_mismatch"),
            ("observed_contracts", "existing_keys"),
            ("observed_contracts", "uploaded_keys"),
            ("observed_contracts", "obsolete_keys"),
        )
    elif scenario_id == "disabled_link_state":
        required_paths = (
            ("inputs", "project_id"),
            ("inputs", "link"),
            ("inputs", "operation_sequence"),
            ("inputs", "disabled_state_before", "list_membership"),
            ("inputs", "disabled_state_after", "list_membership"),
            ("observed_contracts", "disabled_link_behavior"),
            ("observed_contracts", "replace_disabled_membership"),
            ("observed_contracts", "rename_disabled_membership"),
            ("observed_contracts", "delete_behavior"),
            ("observed_contracts", "deleted_projects_entry", "deleted_at"),
            ("observed_contracts", "deleted_projects_entry", "original_link"),
        )

    issues: list[str] = []
    for path in required_paths:
        value = _get_nested_value(data, path)
        if not _provenance_value_is_filled(value):
            issues.append(f"Provenienz Beobachtungsfeld ist nicht ausgefuellt: {'.'.join(path)}.")
    return tuple(issues)


def _get_nested_value(data: Any, path: tuple[str, ...]) -> Any:
    value = data
    for key in path:
        if not isinstance(value, dict):
            return None
        value = value.get(key)
    return value


def _provenance_value_is_filled(value: Any) -> bool:
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, list):
        return bool(value) and all(_provenance_value_is_filled(item) for item in value)
    if isinstance(value, dict):
        return bool(value) and all(_provenance_value_is_filled(item) for item in value.values())
    return value is not None


def _build_scenario_plan(
    scenario: dict[str, Any],
    *,
    captured_root: Path,
    normalized_root: Path,
    provenance_file: str,
) -> GoldenCaptureScenarioPlan:
    scenario_id = str(scenario.get("id", "")).strip()
    required_files = tuple(str(file_name) for file_name in scenario.get("required_files", ()) if str(file_name))
    captured_dir = captured_root / scenario_id
    normalized_dir = normalized_root / scenario_id
    if not captured_dir.exists():
        return GoldenCaptureScenarioPlan(
            scenario_id=scenario_id,
            description=str(scenario.get("description", "") or ""),
            captured_dir=captured_dir,
            normalized_dir=normalized_dir,
            required_files=required_files,
            provenance_file=provenance_file,
        )

    missing_raw: list[str] = []
    missing_normalized: list[str] = []
    drifted: list[str] = []
    for file_name in required_files:
        raw_path = captured_dir / file_name
        normalized_path = normalized_dir / normalized_capture_file_name(file_name)
        if not raw_path.is_file():
            missing_raw.append(file_name)
            continue
        if not normalized_path.is_file():
            missing_normalized.append(normalized_capture_file_name(file_name))
            continue
        actual = normalize_capture_text(file_name, raw_path.read_text(encoding="utf-8"))
        expected = normalized_path.read_text(encoding="utf-8")
        if actual != expected:
            drifted.append(file_name)
    missing_provenance = False
    provenance_issues: tuple[str, ...] = ()
    if provenance_file:
        provenance_path = captured_dir / provenance_file
        if not provenance_path.is_file():
            missing_provenance = True
        else:
            provenance_issues = validate_golden_provenance_file(
                provenance_path,
                scenario_id=scenario_id,
                expected_captured_files=required_files,
                require_filled_observations=True,
            )

    return GoldenCaptureScenarioPlan(
        scenario_id=scenario_id,
        description=str(scenario.get("description", "") or ""),
        captured_dir=captured_dir,
        normalized_dir=normalized_dir,
        required_files=required_files,
        missing_raw_files=tuple(missing_raw),
        missing_normalized_files=tuple(missing_normalized),
        drifted_files=tuple(drifted),
        provenance_file=provenance_file,
        missing_provenance=missing_provenance,
        provenance_issues=provenance_issues,
    )


def _provenance_template_sections(scenario_id: str) -> tuple[dict[str, Any], dict[str, Any]]:
    inputs: dict[str, Any] = {
        "sources": [],
        "s3_prefix": "",
        "crs_cases": [],
        "disabled_state": "",
        "notes": "",
    }
    observed_contracts: dict[str, Any] = {
        "converter_command": "",
        "converter_working_directory": "",
        "s3_extra_args": "",
        "operation_order": [],
        "index_update_order": "",
        "crs_behavior": "",
        "disabled_link_behavior": "",
        "cleanup_behavior": "",
        "rollback_behavior": "",
    }

    if scenario_id == "multi_replace":
        inputs.update(
            {
                "project_id": "",
                "original_link": "",
                "original_disabled_state": "",
                "sources": [
                    {
                        "source": "",
                        "source_type": "",
                        "format": "",
                        "name": "",
                        "slug": "",
                    }
                ],
            }
        )
        observed_contracts.update(
            {
                "operation_order": [
                    "prepare",
                    "collect_existing_keys",
                    "per_source_crs_convert_metadata_upload",
                    "delete_obsolete_keys",
                    "load_index",
                    "update_index",
                    "save_index",
                ],
                "common_crs_decision": "",
                "stale_project_crs_on_mismatch": "",
                "existing_keys": [],
                "uploaded_keys": [],
                "obsolete_keys": [],
                "delete_timing": "before_index_save",
                "delete_errors": [],
                "legacy_uploaded_key_rollback": "none_observed",
            }
        )

    if scenario_id == "disabled_link_state":
        inputs.update(
            {
                "project_id": "",
                "link": "",
                "disabled_state_before": {
                    "list_membership": "",
                    "disabled_at": "",
                    "_link_disabled": "",
                    "link_disabled": "",
                },
                "operation_sequence": [],
                "disabled_state_after": {
                    "list_membership": "",
                    "disabled_at": "",
                    "_link_disabled": "",
                    "link_disabled": "",
                },
            }
        )
        observed_contracts.update(
            {
                "disabled_link_behavior": "",
                "replace_disabled_membership": "",
                "rename_disabled_membership": "",
                "delete_behavior": "",
                "deleted_projects_entry": {
                    "deleted_at": "",
                    "original_link": "",
                },
            }
        )

    return inputs, observed_contracts


def _compare_scenario_outputs(
    scenario: dict[str, Any],
    *,
    actual_root: Path,
    expected_root: Path,
) -> GoldenScenarioComparisonResult:
    scenario_id = str(scenario.get("id", "")).strip()
    actual_dir = actual_root / scenario_id
    expected_dir = expected_root / scenario_id
    file_results: list[GoldenComparisonFileResult] = []
    for file_name in tuple(str(file_name) for file_name in scenario.get("required_files", ()) if str(file_name)):
        actual_path = actual_dir / file_name
        expected_path = expected_dir / normalized_capture_file_name(file_name)
        file_results.append(_compare_output_file(file_name, actual_path=actual_path, expected_path=expected_path))
    return GoldenScenarioComparisonResult(
        scenario_id=scenario_id,
        actual_dir=actual_dir,
        expected_dir=expected_dir,
        files=tuple(file_results),
    )


def _compare_output_file(
    file_name: str,
    *,
    actual_path: Path,
    expected_path: Path,
) -> GoldenComparisonFileResult:
    if not expected_path.is_file():
        return GoldenComparisonFileResult(
            file_name=file_name,
            actual_path=actual_path,
            expected_path=expected_path,
            status="missing_golden",
            detail=f"Normalized legacy Golden fehlt: {expected_path}",
        )
    if not actual_path.is_file():
        return GoldenComparisonFileResult(
            file_name=file_name,
            actual_path=actual_path,
            expected_path=expected_path,
            status="missing_actual",
            detail=f"V2-Ausgabe fehlt: {actual_path}",
        )
    actual = normalize_capture_text(file_name, actual_path.read_text(encoding="utf-8"))
    expected = expected_path.read_text(encoding="utf-8")
    if actual != expected:
        return GoldenComparisonFileResult(
            file_name=file_name,
            actual_path=actual_path,
            expected_path=expected_path,
            status="mismatch",
            detail=f"V2-Ausgabe driftet gegen Legacy-Golden: {file_name}",
        )
    return GoldenComparisonFileResult(
        file_name=file_name,
        actual_path=actual_path,
        expected_path=expected_path,
        status="match",
        detail="OK",
    )


def _selected_scenarios(manifest: dict[str, Any], scenario_id: str | None) -> list[dict[str, Any]]:
    scenarios = manifest.get("scenarios")
    if not isinstance(scenarios, list):
        raise ValueError("Golden manifest must contain a scenarios list.")
    if scenario_id is None:
        return [scenario for scenario in scenarios if isinstance(scenario, dict)]
    selected = [
        scenario
        for scenario in scenarios
        if isinstance(scenario, dict) and str(scenario.get("id", "")).strip() == scenario_id
    ]
    if not selected:
        raise ValueError(f"Unknown Golden scenario: {scenario_id}")
    return selected


def _resolve_manifest_path(manifest_path: Path, value: str) -> Path:
    path = Path(value)
    if path.is_absolute():
        return path
    return path


def _provenance_file_name(manifest: dict[str, Any]) -> str:
    provenance_file = str(manifest.get("provenance_file", "") or "").strip()
    if not provenance_file:
        return ""
    if Path(provenance_file).name != provenance_file:
        raise ValueError(f"Unsafe provenance_file in Golden manifest: {provenance_file}")
    return provenance_file


def _validate_import_source_root(source_dir: Path, *, blocked_roots: tuple[Path, ...]) -> None:
    source_path = source_dir.resolve()
    for blocked_root in blocked_roots:
        blocked_path = blocked_root.resolve()
        if _path_is_relative_to(source_path, blocked_path):
            raise ValueError(f"Golden capture source must not be inside generated Golden output root: {blocked_root}")


def _path_is_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
    except ValueError:
        return False
    return True


def _prepare_scenario_file_import(
    *,
    manifest: dict[str, Any],
    scenario_id: str,
    source_dir: str | Path,
    target_root: Path,
    blocked_roots: tuple[Path, ...],
    overwrite: bool,
    source_label: str,
) -> _GoldenScenarioFileImportPlan:
    scenario = _selected_scenarios(manifest, scenario_id)[0]
    actual_scenario_id = str(scenario.get("id", "")).strip()
    if not actual_scenario_id:
        raise ValueError("Golden scenario is missing an id.")
    if Path(actual_scenario_id).name != actual_scenario_id:
        raise ValueError(f"Golden scenario uses an unsafe id: {actual_scenario_id}")

    required_files = tuple(str(file_name) for file_name in scenario.get("required_files", ()) if str(file_name))
    if not required_files:
        raise ValueError(f"Golden scenario {actual_scenario_id} has no required files.")
    for file_name in required_files:
        if Path(file_name).name != file_name:
            raise ValueError(f"Golden scenario {actual_scenario_id} uses an unsafe file name: {file_name}")

    source_base = Path(source_dir)
    if not source_base.is_dir():
        raise NotADirectoryError(f"{source_label} source directory does not exist: {source_base}")
    _validate_import_source_root(source_base, blocked_roots=blocked_roots)

    target_dir = target_root / actual_scenario_id
    copy_plan = tuple(
        _GoldenScenarioFileCopy(source_path=source_base / file_name, target_path=target_dir / file_name)
        for file_name in required_files
    )

    missing_sources = tuple(file_result.source_path for file_result in copy_plan if not file_result.source_path.is_file())
    if missing_sources:
        missing_list = ", ".join(str(path) for path in missing_sources)
        raise FileNotFoundError(f"Missing required {source_label} file(s): {missing_list}")

    existing_targets = tuple(file_result.target_path for file_result in copy_plan if file_result.target_path.exists())
    if existing_targets and not overwrite:
        existing_list = ", ".join(str(path) for path in existing_targets)
        raise FileExistsError(f"Golden scenario target already exists: {existing_list}")

    for file_result in copy_plan:
        normalize_capture_text(file_result.target_path.name, file_result.source_path.read_text(encoding="utf-8"))

    return _GoldenScenarioFileImportPlan(
        scenario_id=actual_scenario_id,
        source_dir=source_base,
        target_dir=target_dir,
        files=copy_plan,
    )


def _copy_scenario_files(plan: _GoldenScenarioFileImportPlan) -> None:
    plan.target_dir.mkdir(parents=True, exist_ok=True)
    for file_result in plan.files:
        shutil.copyfile(file_result.source_path, file_result.target_path)


def _normalize_scenario(
    scenario: dict[str, Any],
    *,
    captured_root: Path,
    normalized_root: Path,
) -> GoldenCaptureScenarioResult:
    scenario_id = str(scenario.get("id", "")).strip()
    if not scenario_id:
        raise ValueError("Golden scenario is missing an id.")
    required_files = tuple(str(file_name) for file_name in scenario.get("required_files", ()) if str(file_name))
    if not required_files:
        raise ValueError(f"Golden scenario {scenario_id} has no required files.")

    captured_dir = captured_root / scenario_id
    normalized_dir = normalized_root / scenario_id
    if not captured_dir.exists():
        return GoldenCaptureScenarioResult(
            scenario_id=scenario_id,
            status="pending",
            message=f"Legacy capture pending for {scenario_id}",
        )

    normalized_dir.mkdir(parents=True, exist_ok=True)
    written_files: list[GoldenCaptureFile] = []
    for file_name in required_files:
        if Path(file_name).name != file_name:
            raise ValueError(f"Golden scenario {scenario_id} uses an unsafe file name: {file_name}")
        raw_path = captured_dir / file_name
        if not raw_path.is_file():
            raise FileNotFoundError(f"Missing captured legacy file: {raw_path}")
        normalized_path = normalized_dir / normalized_capture_file_name(file_name)
        normalized_text = normalize_capture_text(file_name, raw_path.read_text(encoding="utf-8"))
        normalized_path.write_text(normalized_text, encoding="utf-8", newline="\n")
        written_files.append(GoldenCaptureFile(raw_path=raw_path, normalized_path=normalized_path))

    return GoldenCaptureScenarioResult(
        scenario_id=scenario_id,
        status="normalized",
        files=tuple(written_files),
        message=f"Normalized {len(written_files)} file(s) for {scenario_id}",
    )


__all__ = [
    "GoldenCaptureFile",
    "GoldenCaptureImportResult",
    "GoldenCaptureInitializationResult",
    "GoldenCapturePlan",
    "GoldenCaptureScenarioResult",
    "GoldenCaptureScenarioPlan",
    "GoldenComparisonFileResult",
    "GoldenComparisonReport",
    "GoldenImportedCaptureFile",
    "GoldenImportedV2OutputFile",
    "GoldenScenarioComparisonResult",
    "GoldenV2OutputImportResult",
    "GoldenV2OutputFreshnessReport",
    "GoldenV2OutputReadinessReport",
    "GoldenReadinessIssue",
    "GoldenReadinessReport",
    "build_golden_capture_plan",
    "build_golden_provenance_template",
    "check_golden_capture_readiness",
    "check_v2_output_freshness",
    "check_v2_output_readiness",
    "compare_golden_outputs",
    "golden_manifest_scenario_ids",
    "import_golden_capture_files",
    "import_v2_golden_output_files",
    "initialize_golden_capture_scenario",
    "load_golden_manifest",
    "normalize_capture_text",
    "normalize_captured_golden_files",
    "normalized_capture_file_name",
    "validate_golden_provenance",
    "validate_golden_provenance_file",
]
