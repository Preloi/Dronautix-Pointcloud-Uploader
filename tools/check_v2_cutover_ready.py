"""Fail the V2 cutover gate when any mandatory release evidence is missing."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from dronautix_uploader.core.cutover_acceptance import (
    build_acceptance_evidence_template,
    evaluate_acceptance_evidence,
    load_acceptance_evidence,
)
from dronautix_uploader.core.golden_capture import check_golden_capture_readiness
from dronautix_uploader.core.golden_capture import check_v2_output_freshness
from dronautix_uploader.core.golden_capture import check_v2_output_readiness
from dronautix_uploader.core.golden_capture import compare_golden_outputs
from dronautix_uploader.core.golden_capture import golden_manifest_scenario_ids
from tools.check_v2_final_packaging_contract import (
    CANDIDATE_MANIFEST_PATH,
    build_final_packaging_contract,
    load_candidate_manifest,
    validate_candidate_manifest_file,
    validate_final_packaging_contract,
)


DEFAULT_MANIFEST = Path("tests/golden/manifest.json")
DEFAULT_ACCEPTANCE = Path("artifacts/v2-cutover-acceptance.json")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Check strict V2 cutover readiness gates.")
    parser.add_argument(
        "--manifest",
        default=str(DEFAULT_MANIFEST),
        help="Path to tests/golden/manifest.json.",
    )
    parser.add_argument(
        "--acceptance",
        default=str(DEFAULT_ACCEPTANCE),
        help="Path to the local V2 cutover acceptance evidence JSON.",
    )
    parser.add_argument(
        "--v2-output-root",
        default=None,
        help="Root containing V2 outputs by Golden scenario. Defaults to manifest v2_output_root.",
    )
    parser.add_argument(
        "--candidate-manifest",
        default=None,
        help="Path to the final-V2 candidate release manifest. Defaults to acceptance evidence or artifacts path.",
    )
    parser.add_argument(
        "--write-template",
        action="store_true",
        help="Write an acceptance evidence template to --acceptance and exit.",
    )
    args = parser.parse_args(argv)
    required_s3_scenarios = golden_manifest_scenario_ids(args.manifest)

    if args.write_template:
        output_path = Path(args.acceptance)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(
            json.dumps(
                build_acceptance_evidence_template(required_s3_scenarios=required_s3_scenarios),
                indent=2,
                ensure_ascii=False,
            )
            + "\n",
            encoding="utf-8",
            newline="\n",
        )
        print(f"Acceptance evidence template written: {output_path}")
        return 0

    acceptance_path = Path(args.acceptance)
    acceptance_exists = acceptance_path.is_file()
    acceptance_data = load_acceptance_evidence(acceptance_path)
    candidate_manifest_path = _candidate_manifest_path(args.candidate_manifest, acceptance_data)
    require_candidate_sha = _github_asset_sha_claims_passed(acceptance_data)
    candidate_sha = _candidate_sha_for_contract(acceptance_data) if require_candidate_sha else ""
    packaging_contract = build_final_packaging_contract(candidate_sha)
    packaging_contract["isolated_paths"]["manifest_path"] = candidate_manifest_path.as_posix()
    golden_report = check_golden_capture_readiness(args.manifest)
    v2_output_report = check_v2_output_readiness(args.manifest, actual_root=args.v2_output_root)
    v2_freshness_report = check_v2_output_freshness(args.manifest, actual_root=args.v2_output_root)
    golden_comparison = compare_golden_outputs(args.manifest, actual_root=args.v2_output_root)
    packaging_issues = validate_final_packaging_contract(packaging_contract)
    candidate_manifest_issues = validate_candidate_manifest_file(
        candidate_manifest_path,
        packaging_contract,
        require_valid_sha=require_candidate_sha,
    )
    candidate_contract_for_acceptance = packaging_contract
    if not candidate_manifest_issues:
        candidate_contract_for_acceptance = load_candidate_manifest(candidate_manifest_path)
    acceptance_results = evaluate_acceptance_evidence(
        acceptance_data,
        candidate_contract=candidate_contract_for_acceptance,
        required_s3_scenarios=required_s3_scenarios,
    )

    blocked = False
    print(f"Golden scenarios captured: {golden_report.captured_count}/{golden_report.scenario_count}")
    if golden_report.ready:
        print("[OK] Golden Masters")
    else:
        blocked = True
        print("[BLOCKED] Golden Masters")
        for issue in golden_report.issues:
            print(f"- {issue.scenario_id}: {issue.message}")

    print(f"V2 Golden outputs present: {v2_output_report.output_count}/{v2_output_report.scenario_count}")
    if v2_output_report.ready:
        print("[OK] V2 output files")
    else:
        blocked = True
        print("[BLOCKED] V2 output files")
        for issue in v2_output_report.issues:
            print(f"- {issue.scenario_id}: {issue.message}")

    print(
        f"V2 Golden outputs current: {v2_freshness_report.checked_count}/"
        f"{v2_freshness_report.scenario_count}"
    )
    if v2_freshness_report.ready:
        print("[OK] V2 output freshness")
    else:
        blocked = True
        print("[BLOCKED] V2 output freshness")
        for issue in v2_freshness_report.issues:
            print(f"- {issue.scenario_id}: {issue.message}")

    print(
        f"V2 Golden comparisons matching: {golden_comparison.ready_count}/"
        f"{golden_comparison.scenario_count}"
    )
    if golden_comparison.ready:
        print("[OK] V2 outputs match Golden Masters")
    else:
        blocked = True
        print("[BLOCKED] V2 outputs match Golden Masters")
        for scenario in golden_comparison.scenarios:
            if scenario.ready:
                continue
            print(f"- {scenario.scenario_id}:")
            for file_result in scenario.files:
                if file_result.match:
                    continue
                print(f"  - {file_result.file_name}: {file_result.detail}")

    if packaging_issues:
        blocked = True
        print("[BLOCKED] Final-V2 packaging contract")
        for issue in packaging_issues:
            print(f"- {issue}")
    else:
        print("[OK] Final-V2 packaging contract")

    if candidate_manifest_issues:
        blocked = True
        print("[BLOCKED] Final-V2 candidate manifest")
        for issue in candidate_manifest_issues:
            print(f"- {issue}")
    else:
        print(f"[OK] Final-V2 candidate manifest: {candidate_manifest_path}")

    if not acceptance_exists:
        blocked = True
        print(f"[BLOCKED] Akzeptanz-Evidenzdatei fehlt: {acceptance_path}")
        print(f"- Vorlage erzeugen: python tools/check_v2_cutover_ready.py --acceptance {acceptance_path} --write-template")

    for result in acceptance_results:
        if result.complete:
            print(f"[OK] {result.label}: {result.detail}")
            continue
        blocked = True
        print(f"[BLOCKED] {result.label}: {result.detail}")

    if blocked:
        print("V2 cutover gate: BLOCKED")
        return 1
    print("V2 cutover gate: OK")
    return 0


def _candidate_manifest_path(cli_value: str | None, acceptance_data: dict) -> Path:
    if cli_value:
        return Path(cli_value)
    value = str(acceptance_data.get("candidate_manifest_path", "") or "").strip()
    return Path(value) if value else CANDIDATE_MANIFEST_PATH


def _github_asset_sha_claims_passed(acceptance_data: dict) -> bool:
    gates = acceptance_data.get("gates", {})
    gate = gates.get("github_asset_sha") if isinstance(gates, dict) else None
    if not isinstance(gate, dict):
        return False
    return gate.get("status") == "passed" or gate.get("passed") is True


def _candidate_sha_for_contract(acceptance_data: dict) -> str:
    direct_sha = str(acceptance_data.get("candidate_installer_sha256", "") or "").strip().lower()
    if direct_sha:
        return direct_sha
    gates = acceptance_data.get("gates", {})
    gate = gates.get("github_asset_sha") if isinstance(gates, dict) else None
    if not isinstance(gate, dict):
        return ""
    return str(gate.get("manifest_sha256", "") or gate.get("asset_sha256", "") or "").strip().lower()


if __name__ == "__main__":
    raise SystemExit(main())
