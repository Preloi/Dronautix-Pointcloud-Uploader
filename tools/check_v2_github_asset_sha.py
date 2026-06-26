"""Verify the final-V2 GitHub release asset SHA and optionally update acceptance evidence."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from dronautix_uploader.core.cutover_acceptance import build_acceptance_evidence_template, load_acceptance_evidence
from dronautix_uploader.core.github_asset_verification import (
    github_asset_sha_result_to_acceptance_gate,
    merge_github_asset_sha_into_acceptance_evidence,
    verify_github_asset_sha,
)
from dronautix_uploader.core.golden_capture import golden_manifest_scenario_ids
from tools.check_v2_cutover_ready import DEFAULT_ACCEPTANCE, DEFAULT_MANIFEST
from tools.check_v2_final_packaging_contract import CANDIDATE_MANIFEST_PATH, load_candidate_manifest


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--candidate-manifest",
        default=str(CANDIDATE_MANIFEST_PATH),
        help="Path to artifacts/v2-final-candidate-release.json.",
    )
    parser.add_argument("--acceptance", default=str(DEFAULT_ACCEPTANCE), help="Acceptance evidence JSON to update.")
    parser.add_argument("--manifest", default=str(DEFAULT_MANIFEST), help="Golden manifest for template scenarios.")
    parser.add_argument(
        "--asset-path",
        default="",
        help="Optional path to a predownloaded GitHub release asset. The file name must match the manifest.",
    )
    parser.add_argument(
        "--download-dir",
        default="",
        help="Optional directory where the release asset should be downloaded before hashing.",
    )
    parser.add_argument("--timeout", type=float, default=60.0, help="Download timeout in seconds.")
    parser.add_argument("--write-acceptance", action="store_true", help="Merge the SHA gate into --acceptance.")
    parser.add_argument("--json", action="store_true", help="Print machine-readable verification output.")
    args = parser.parse_args(argv)

    candidate_manifest_path = Path(args.candidate_manifest)
    try:
        candidate_contract = load_candidate_manifest(candidate_manifest_path)
    except Exception as exc:
        print(f"Candidate-Manifest konnte nicht gelesen werden: {exc}")
        return 1

    result = verify_github_asset_sha(
        candidate_contract,
        asset_path=args.asset_path or None,
        download_dir=args.download_dir or None,
        timeout_seconds=args.timeout,
    )
    gate = github_asset_sha_result_to_acceptance_gate(result)

    if args.json:
        print(json.dumps({"passed": result.passed, "gate": gate}, indent=2, ensure_ascii=False))
    else:
        print(f"GitHub Asset SHA: {'OK' if result.passed else 'BLOCKED'}")
        print(f"- repo: {result.repo}")
        print(f"- release_tag: {result.release_tag}")
        print(f"- asset_name: {result.asset_name}")
        print(f"- manifest_sha256: {result.manifest_sha256 or '<missing>'}")
        print(f"- asset_sha256: {result.asset_sha256 or '<not verified>'}")
        print(f"- detail: {result.message}")

    if args.write_acceptance:
        acceptance_path = Path(args.acceptance)
        acceptance_data = load_acceptance_evidence(acceptance_path)
        if not acceptance_data:
            acceptance_data = build_acceptance_evidence_template(
                required_s3_scenarios=golden_manifest_scenario_ids(args.manifest)
            )
        acceptance_path.parent.mkdir(parents=True, exist_ok=True)
        acceptance_path.write_text(
            json.dumps(
                merge_github_asset_sha_into_acceptance_evidence(
                    acceptance_data,
                    result,
                    candidate_contract,
                    candidate_manifest_path=candidate_manifest_path,
                ),
                indent=2,
                ensure_ascii=False,
            )
            + "\n",
            encoding="utf-8",
            newline="\n",
        )
        print(f"Acceptance GitHub-SHA gate updated: {acceptance_path}")

    return 0 if result.passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
