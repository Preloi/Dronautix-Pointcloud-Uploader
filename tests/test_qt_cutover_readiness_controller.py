from dronautix_uploader.core.cutover_acceptance import (
    GITHUB_ASSET_SHA,
    LEGACY_UPDATE,
    REAL_S3_ACCEPTANCE,
    AcceptanceGateResult,
)
from dronautix_uploader.core.golden_capture import GoldenReadinessIssue, GoldenReadinessReport
from dronautix_uploader.qt_app.cutover_readiness_controller import CutoverReadinessController


def test_cutover_readiness_controller_reports_all_gates_ready(tmp_path):
    preview_files = _make_preview_files(tmp_path)

    def golden_checker(path):
        return GoldenReadinessReport(ready=True, scenario_count=2, captured_count=2)

    def packaging_builder():
        return {"contract": "ok"}

    def packaging_validator(contract):
        assert contract == {"contract": "ok"}
        return ()

    def acceptance_loader(path):
        return {"schema_version": 1}

    def acceptance_evaluator(evidence, *, candidate_contract=None, required_s3_scenarios=()):
        assert candidate_contract == {"contract": "ok"}
        return (
            AcceptanceGateResult(REAL_S3_ACCEPTANCE, "Echter S3-Akzeptanztest", True, "OK"),
            AcceptanceGateResult(GITHUB_ASSET_SHA, "GitHub Asset SHA", True, "OK"),
            AcceptanceGateResult(LEGACY_UPDATE, "Altversions-Update", True, "OK"),
        )

    controller = CutoverReadinessController(
        preview_files=preview_files,
        golden_checker=golden_checker,
        golden_comparator=_ready_golden_comparator,
        packaging_builder=packaging_builder,
        packaging_validator=packaging_validator,
        acceptance_loader=acceptance_loader,
        acceptance_evaluator=acceptance_evaluator,
    )

    readiness = controller.readiness(runtime_connected=True)

    assert readiness.ready
    assert readiness.completed_required_count == 8
    assert readiness.first_open_item is None


def test_cutover_readiness_controller_surfaces_missing_acceptance_evidence(tmp_path):
    preview_files = _make_preview_files(tmp_path)

    controller = CutoverReadinessController(
        preview_files=preview_files,
        golden_checker=lambda path: GoldenReadinessReport(ready=True, scenario_count=1, captured_count=1),
        golden_comparator=_ready_golden_comparator,
        packaging_builder=lambda: {"contract": "ok"},
        packaging_validator=lambda contract: (),
        acceptance_loader=lambda path: {},
    )

    readiness = controller.readiness(runtime_connected=True)

    assert not readiness.ready
    assert readiness.completed_required_count == 5
    assert readiness.first_open_item.name == "Echter S3-Akzeptanztest"
    assert "schema_version=1 fehlt" in readiness.first_open_item.detail


def test_cutover_readiness_controller_keeps_packaging_issues_local_to_packaging_gate(tmp_path):
    preview_files = _make_preview_files(tmp_path)
    observed_contracts = []

    def acceptance_evaluator(evidence, *, candidate_contract=None, required_s3_scenarios=()):
        observed_contracts.append(candidate_contract)
        return (
            AcceptanceGateResult(REAL_S3_ACCEPTANCE, "Echter S3-Akzeptanztest", True, "OK"),
            AcceptanceGateResult(GITHUB_ASSET_SHA, "GitHub Asset SHA", True, "OK"),
            AcceptanceGateResult(LEGACY_UPDATE, "Altversions-Update", True, "OK"),
        )

    controller = CutoverReadinessController(
        preview_files=preview_files,
        golden_checker=lambda path: GoldenReadinessReport(ready=True, scenario_count=1, captured_count=1),
        golden_comparator=_ready_golden_comparator,
        packaging_builder=lambda: {"contract": "blocked"},
        packaging_validator=lambda contract: ("Final-Kandidat darf nicht latest-release.json schreiben.",),
        acceptance_loader=lambda path: {"schema_version": 1},
        acceptance_evaluator=acceptance_evaluator,
    )

    readiness = controller.readiness(runtime_connected=True)
    packaging_item = next(item for item in readiness.items if item.name == "Final-V2-Packaging")

    assert not readiness.ready
    assert packaging_item.complete is False
    assert packaging_item.detail == "Final-Kandidat darf nicht latest-release.json schreiben."
    assert observed_contracts == [None]


def test_cutover_readiness_controller_handles_malformed_acceptance_json(tmp_path):
    preview_files = _make_preview_files(tmp_path)

    def broken_loader(path):
        raise ValueError("Akzeptanz-Evidenz ist kein gueltiges JSON")

    controller = CutoverReadinessController(
        preview_files=preview_files,
        golden_checker=lambda path: GoldenReadinessReport(ready=True, scenario_count=1, captured_count=1),
        golden_comparator=_ready_golden_comparator,
        packaging_builder=lambda: {"contract": "ok"},
        packaging_validator=lambda contract: (),
        acceptance_loader=broken_loader,
    )

    readiness = controller.readiness(runtime_connected=True)
    first_acceptance_item = next(item for item in readiness.items if item.name == "Echter S3-Akzeptanztest")

    assert not readiness.ready
    assert first_acceptance_item.complete is False
    assert "Akzeptanz-Evidenz konnte nicht gelesen werden" in first_acceptance_item.detail


def test_cutover_readiness_controller_keeps_first_golden_issue_actionable(tmp_path):
    preview_files = _make_preview_files(tmp_path)

    controller = CutoverReadinessController(
        preview_files=preview_files,
        golden_checker=lambda path: GoldenReadinessReport(
            ready=False,
            scenario_count=3,
            captured_count=1,
            issues=(GoldenReadinessIssue("multi_mix", "Legacy capture fehlt."),),
        ),
        golden_comparator=_ready_golden_comparator,
        packaging_builder=lambda: {"contract": "ok"},
        packaging_validator=lambda contract: (),
        acceptance_loader=lambda path: {},
    )

    readiness = controller.readiness(runtime_connected=True)

    assert not readiness.ready
    assert readiness.first_open_item.name == "Golden Masters"
    assert "1/3 Legacy-Golden-Szenarien gecaptured" in readiness.first_open_item.detail
    assert "multi_mix: Legacy capture fehlt." in readiness.first_open_item.detail


def test_cutover_readiness_controller_surfaces_v2_golden_comparison_gate(tmp_path):
    preview_files = _make_preview_files(tmp_path)

    controller = CutoverReadinessController(
        preview_files=preview_files,
        golden_checker=lambda path: GoldenReadinessReport(ready=True, scenario_count=1, captured_count=1),
        golden_comparator=lambda *args, **kwargs: _blocked_golden_comparison(),
        packaging_builder=lambda: {"contract": "ok"},
        packaging_validator=lambda contract: (),
        acceptance_loader=lambda path: {},
    )

    readiness = controller.readiness(runtime_connected=True)

    assert not readiness.ready
    assert readiness.first_open_item.name == "V2-Golden-Vergleich"
    assert "multi_replace: V2-Ausgabe fehlt" in readiness.first_open_item.detail


def test_cutover_readiness_controller_passes_manifest_scenarios_to_acceptance_evaluator(tmp_path):
    preview_files = _make_preview_files(tmp_path)
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(
        (
            '{"schema_version":1,"captured_root":"x","normalized_root":"y",'
            '"scenarios":[{"id":"single_copc_upload","required_files":["projects_index.json"]}]}'
        ),
        encoding="utf-8",
    )
    observed_scenarios = []

    def acceptance_evaluator(evidence, *, candidate_contract=None, required_s3_scenarios=()):
        observed_scenarios.append(required_s3_scenarios)
        return (
            AcceptanceGateResult(REAL_S3_ACCEPTANCE, "Echter S3-Akzeptanztest", True, "OK"),
            AcceptanceGateResult(GITHUB_ASSET_SHA, "GitHub Asset SHA", True, "OK"),
            AcceptanceGateResult(LEGACY_UPDATE, "Altversions-Update", True, "OK"),
        )

    controller = CutoverReadinessController(
        golden_manifest_path=manifest_path,
        preview_files=preview_files,
        golden_checker=lambda path: GoldenReadinessReport(ready=True, scenario_count=1, captured_count=1),
        golden_comparator=_ready_golden_comparator,
        packaging_builder=lambda: {"contract": "ok"},
        packaging_validator=lambda contract: (),
        acceptance_loader=lambda path: {"schema_version": 1},
        acceptance_evaluator=acceptance_evaluator,
    )

    readiness = controller.readiness(runtime_connected=True)

    assert readiness.ready
    assert observed_scenarios == [("single_copc_upload",)]


def _make_preview_files(tmp_path):
    paths = (
        tmp_path / "Dronautix_Pointcloud_Uploader_v2.py",
        tmp_path / "build_v2_preview.py",
        tmp_path / "requirements-v2-preview.txt",
    )
    for path in paths:
        path.write_text("", encoding="utf-8")
    return paths


class _FileResult:
    def __init__(self, match, detail):
        self.match = match
        self.detail = detail


class _ScenarioResult:
    def __init__(self, ready, files):
        self.ready = ready
        self.files = tuple(files)
        self.scenario_id = "multi_replace"


class _ComparisonReport:
    def __init__(self, ready):
        self.ready = ready
        self.ready_count = 1 if ready else 0
        self.scenario_count = 1
        self.scenarios = () if ready else (_ScenarioResult(False, (_FileResult(False, "V2-Ausgabe fehlt."),)),)


def _ready_golden_comparator(*args, **kwargs):
    return _ComparisonReport(True)


def _blocked_golden_comparison():
    return _ComparisonReport(False)
