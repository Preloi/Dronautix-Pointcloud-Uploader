"""UI-free Cutover readiness aggregation for the Qt dashboard."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from dronautix_uploader.core.cutover_acceptance import (
    GITHUB_ASSET_SHA,
    LEGACY_UPDATE,
    REAL_S3_ACCEPTANCE,
    AcceptanceGateResult,
    evaluate_acceptance_evidence,
    load_acceptance_evidence,
)
from dronautix_uploader.core.golden_capture import (
    GoldenComparisonReport,
    GoldenReadinessReport,
    check_golden_capture_readiness,
    compare_golden_outputs,
    golden_manifest_scenario_ids,
)
from tools.check_v2_final_packaging_contract import (
    build_final_packaging_contract,
    validate_final_packaging_contract,
)

from .dashboard_settings_model import CutoverChecklistItem, CutoverReadiness


DEFAULT_GOLDEN_MANIFEST = Path("tests/golden/manifest.json")
DEFAULT_ACCEPTANCE_EVIDENCE = Path("artifacts/v2-cutover-acceptance.json")
DEFAULT_PREVIEW_FILES = (
    Path("Dronautix_Pointcloud_Uploader_v2.py"),
    Path("build_v2_preview.py"),
    Path("requirements-v2-preview.txt"),
)


GoldenChecker = Callable[[str | Path], GoldenReadinessReport]
GoldenComparator = Callable[..., GoldenComparisonReport]
PackagingBuilder = Callable[[], dict[str, Any]]
PackagingValidator = Callable[[dict[str, Any]], Iterable[str]]
AcceptanceLoader = Callable[[str | Path], dict[str, Any]]
AcceptanceEvaluator = Callable[..., tuple[AcceptanceGateResult, ...]]


@dataclass(frozen=True)
class ReadinessProbe:
    complete: bool
    detail: str


class CutoverReadinessController:
    """Build the dashboard cutover checklist from local release evidence."""

    def __init__(
        self,
        *,
        golden_manifest_path: str | Path = DEFAULT_GOLDEN_MANIFEST,
        v2_output_root: str | Path | None = None,
        acceptance_evidence_path: str | Path = DEFAULT_ACCEPTANCE_EVIDENCE,
        preview_files: Iterable[str | Path] = DEFAULT_PREVIEW_FILES,
        golden_checker: GoldenChecker = check_golden_capture_readiness,
        golden_comparator: GoldenComparator = compare_golden_outputs,
        packaging_builder: PackagingBuilder = build_final_packaging_contract,
        packaging_validator: PackagingValidator | None = None,
        acceptance_loader: AcceptanceLoader = load_acceptance_evidence,
        acceptance_evaluator: AcceptanceEvaluator = evaluate_acceptance_evidence,
    ):
        self._golden_manifest_path = Path(golden_manifest_path)
        self._v2_output_root = Path(v2_output_root) if v2_output_root is not None else None
        self._acceptance_evidence_path = Path(acceptance_evidence_path)
        self._preview_files = tuple(Path(path) for path in preview_files)
        self._golden_checker = golden_checker
        self._golden_comparator = golden_comparator
        self._packaging_builder = packaging_builder
        self._packaging_validator = packaging_validator or _validate_packaging_contract
        self._acceptance_loader = acceptance_loader
        self._acceptance_evaluator = acceptance_evaluator

    def readiness(self, *, runtime_connected: bool = False) -> CutoverReadiness:
        packaging_contract = self._build_packaging_contract()
        packaging_probe = self._check_packaging_contract(packaging_contract)
        required_s3_scenarios = self._required_s3_scenarios()
        acceptance_results = self._check_acceptance_evidence(
            packaging_contract if packaging_probe.complete else None,
            required_s3_scenarios=required_s3_scenarios,
        )
        acceptance_by_gate = {result.gate_id: result for result in acceptance_results}
        golden_probe = self._check_golden_readiness()
        golden_comparison_probe = self._check_golden_comparison()
        preview_probe = self._check_preview_packaging()

        return CutoverReadiness(
            items=(
                CutoverChecklistItem(
                    "Runtime verbunden",
                    runtime_connected,
                    (
                        "Qt-Runtime hat echte S3-Service-Controller geladen."
                        if runtime_connected
                        else "Qt-Runtime ist noch nicht mit echten S3-Service-Controllern verbunden."
                    ),
                ),
                CutoverChecklistItem("Golden Masters", golden_probe.complete, golden_probe.detail),
                CutoverChecklistItem(
                    "V2-Golden-Vergleich",
                    golden_comparison_probe.complete,
                    golden_comparison_probe.detail,
                ),
                CutoverChecklistItem("Preview-Paket getrennt", preview_probe.complete, preview_probe.detail),
                CutoverChecklistItem("Final-V2-Packaging", packaging_probe.complete, packaging_probe.detail),
                _acceptance_item(
                    acceptance_by_gate.get(REAL_S3_ACCEPTANCE),
                    "Echter S3-Akzeptanztest",
                    "S3-Akzeptanz-Evidenz fehlt.",
                ),
                _acceptance_item(
                    acceptance_by_gate.get(GITHUB_ASSET_SHA),
                    "GitHub Asset SHA",
                    "GitHub-Asset-SHA-Evidenz fehlt.",
                ),
                _acceptance_item(
                    acceptance_by_gate.get(LEGACY_UPDATE),
                    "Altversions-Update",
                    "Altversions-Update-Evidenz fehlt.",
                ),
            )
        )

    def _check_golden_readiness(self) -> ReadinessProbe:
        try:
            report = self._golden_checker(self._golden_manifest_path)
        except Exception as exc:
            return ReadinessProbe(False, f"Golden-Readiness konnte nicht gelesen werden: {exc}")

        detail = f"{report.captured_count}/{report.scenario_count} Legacy-Golden-Szenarien gecaptured."
        if report.ready:
            return ReadinessProbe(True, detail)
        first_issue = report.issues[0] if report.issues else None
        if first_issue is None:
            return ReadinessProbe(False, detail)
        return ReadinessProbe(False, f"{detail} {first_issue.scenario_id}: {first_issue.message}")

    def _check_golden_comparison(self) -> ReadinessProbe:
        try:
            report = self._golden_comparator(
                self._golden_manifest_path,
                actual_root=self._v2_output_root,
            )
        except Exception as exc:
            return ReadinessProbe(False, f"V2-Golden-Vergleich konnte nicht gelesen werden: {exc}")

        detail = f"{report.ready_count}/{report.scenario_count} V2-Golden-Szenarien matchen."
        if report.ready:
            return ReadinessProbe(True, detail)
        for scenario in report.scenarios:
            if scenario.ready:
                continue
            for file_result in scenario.files:
                if file_result.match:
                    continue
                return ReadinessProbe(False, f"{detail} {scenario.scenario_id}: {file_result.detail}")
        return ReadinessProbe(False, detail)

    def _check_preview_packaging(self) -> ReadinessProbe:
        missing = tuple(str(path) for path in self._preview_files if not path.exists())
        if missing:
            return ReadinessProbe(False, f"Preview-Dateien fehlen: {', '.join(missing)}.")
        return ReadinessProbe(True, "Preview-Build nutzt separaten Entrypoint, Build-Skript und Requirements.")

    def _build_packaging_contract(self) -> dict[str, Any]:
        try:
            return self._packaging_builder()
        except Exception:
            return {}

    def _check_packaging_contract(self, contract: dict[str, Any]) -> ReadinessProbe:
        if not contract:
            return ReadinessProbe(False, "Final-V2-Candidate-Vertrag konnte nicht gebaut werden.")
        try:
            issues = tuple(self._packaging_validator(contract))
        except Exception as exc:
            return ReadinessProbe(False, f"Final-V2-Candidate-Vertrag konnte nicht geprüft werden: {exc}")
        if issues:
            return ReadinessProbe(False, issues[0])
        return ReadinessProbe(True, "Final-V2-Candidate-Vertrag OK.")

    def _required_s3_scenarios(self) -> tuple[str, ...]:
        try:
            return golden_manifest_scenario_ids(self._golden_manifest_path)
        except Exception:
            return ()

    def _check_acceptance_evidence(
        self,
        contract: dict[str, Any] | None,
        *,
        required_s3_scenarios: tuple[str, ...],
    ) -> tuple[AcceptanceGateResult, ...]:
        try:
            evidence = self._acceptance_loader(self._acceptance_evidence_path)
            return tuple(
                self._acceptance_evaluator(
                    evidence,
                    candidate_contract=contract,
                    required_s3_scenarios=required_s3_scenarios,
                )
            )
        except Exception as exc:
            detail = f"Akzeptanz-Evidenz konnte nicht gelesen werden: {exc}"
            return (
                AcceptanceGateResult(REAL_S3_ACCEPTANCE, "Echter S3-Akzeptanztest", False, detail),
                AcceptanceGateResult(GITHUB_ASSET_SHA, "GitHub Asset SHA", False, detail),
                AcceptanceGateResult(LEGACY_UPDATE, "Altversions-Update", False, detail),
            )


def _validate_packaging_contract(contract: dict[str, Any]) -> tuple[str, ...]:
    return validate_final_packaging_contract(contract)


def _acceptance_item(
    result: AcceptanceGateResult | None,
    fallback_label: str,
    fallback_detail: str,
) -> CutoverChecklistItem:
    if result is None:
        return CutoverChecklistItem(fallback_label, False, fallback_detail)
    return CutoverChecklistItem(result.label, result.complete, result.detail)


__all__ = [
    "CutoverReadinessController",
    "DEFAULT_ACCEPTANCE_EVIDENCE",
    "DEFAULT_GOLDEN_MANIFEST",
    "DEFAULT_PREVIEW_FILES",
    "ReadinessProbe",
]
