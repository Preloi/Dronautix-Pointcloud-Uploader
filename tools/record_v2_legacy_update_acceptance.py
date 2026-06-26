"""Record manual installed-legacy update evidence for the V2 cutover gate."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app_version import APP_VERSION
from dronautix_uploader.core.cutover_acceptance import build_acceptance_evidence_template, load_acceptance_evidence
from dronautix_uploader.core.golden_capture import golden_manifest_scenario_ids
from dronautix_uploader.core.legacy_update_acceptance import (
    build_legacy_update_acceptance_result,
    legacy_update_result_to_acceptance_gate,
    merge_legacy_update_into_acceptance_evidence,
)
from tools.check_v2_cutover_ready import DEFAULT_ACCEPTANCE, DEFAULT_MANIFEST


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--from-version", required=True, help="Installed legacy version that observed the update.")
    parser.add_argument("--to-version", default=APP_VERSION, help="Final V2 version reached after update.")
    parser.add_argument("--acceptance", default=str(DEFAULT_ACCEPTANCE), help="Acceptance evidence JSON to update.")
    parser.add_argument("--manifest", default=str(DEFAULT_MANIFEST), help="Golden manifest for template scenarios.")
    parser.add_argument("--notes", default="", help="Operator notes for the update test.")
    parser.add_argument("--completed-at-utc", default="", help="Optional fixed completion timestamp.")
    parser.add_argument("--installed-app-id-preserved", action="store_true")
    parser.add_argument("--update-prompt-seen", action="store_true")
    parser.add_argument("--download-sha-verified", action="store_true")
    parser.add_argument("--post-update-launch-ok", action="store_true")
    parser.add_argument("--legacy-config-or-keyring-available", action="store_true")
    parser.add_argument("--write-acceptance", action="store_true", help="Merge the gate into --acceptance.")
    parser.add_argument("--json", action="store_true", help="Print machine-readable gate output.")
    args = parser.parse_args(argv)

    result = build_legacy_update_acceptance_result(
        from_version=args.from_version,
        to_version=args.to_version,
        installed_app_id_preserved=args.installed_app_id_preserved,
        update_prompt_seen=args.update_prompt_seen,
        download_sha_verified=args.download_sha_verified,
        post_update_launch_ok=args.post_update_launch_ok,
        legacy_config_or_keyring_available=args.legacy_config_or_keyring_available,
        notes=args.notes,
        completed_at_utc=args.completed_at_utc,
    )
    gate = legacy_update_result_to_acceptance_gate(result)

    if args.json:
        print(json.dumps({"passed": result.passed, "gate": gate}, indent=2, ensure_ascii=False))
    else:
        print(f"Altversions-Update: {'OK' if result.passed else 'BLOCKED'}")
        print(f"- from_version: {result.from_version or '<missing>'}")
        print(f"- to_version: {result.to_version or '<missing>'}")
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
                merge_legacy_update_into_acceptance_evidence(acceptance_data, result),
                indent=2,
                ensure_ascii=False,
            )
            + "\n",
            encoding="utf-8",
            newline="\n",
        )
        print(f"Acceptance legacy-update gate updated: {acceptance_path}")

    return 0 if result.passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
