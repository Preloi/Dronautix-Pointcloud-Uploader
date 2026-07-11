"""Run the isolated real-S3 V2 acceptance smoke.

The smoke writes only under an isolated test prefix and uses isolated metadata
keys. It requires an explicit confirmation flag because it performs real S3
uploads/deletes.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from dronautix_uploader.adapters.runtime_services import load_project_management_runtime_config
from dronautix_uploader.core.cutover_acceptance import build_acceptance_evidence_template, load_acceptance_evidence
from dronautix_uploader.core.golden_capture import golden_manifest_scenario_ids
from dronautix_uploader.core.s3_acceptance_smoke import (
    S3AcceptanceSmokeConfig,
    merge_s3_smoke_into_acceptance_evidence,
    run_v2_s3_acceptance_smoke,
)


DEFAULT_MANIFEST = Path("tests/golden/manifest.json")
DEFAULT_ACCEPTANCE = Path("artifacts/v2-cutover-acceptance.json")
DEFAULT_RESULT = Path("artifacts/v2-s3-acceptance-smoke.json")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", default=str(DEFAULT_MANIFEST), help="Golden manifest for required scenarios.")
    parser.add_argument("--acceptance", default=str(DEFAULT_ACCEPTANCE), help="Acceptance evidence JSON to update.")
    parser.add_argument("--result", default=str(DEFAULT_RESULT), help="Detailed smoke result JSON path.")
    parser.add_argument("--run-id", default="", help="Optional stable run id for S3 test prefixes.")
    parser.add_argument("--bucket", default="", help="Override bucket from config.")
    parser.add_argument("--region", default="", help="Override region from config.")
    parser.add_argument("--config", default="", help="Optional config.json path.")
    parser.add_argument("--final-config", action="store_true", help="Use final app config path instead of preview path.")
    parser.add_argument("--no-cleanup", action="store_true", help="Leave acceptance test objects in S3 for inspection.")
    parser.add_argument("--write-acceptance", action="store_true", help="Merge the S3 gate result into --acceptance.")
    parser.add_argument(
        "--confirm-real-s3-writes",
        action="store_true",
        help="Required. Confirms this command may upload/delete isolated S3 test objects.",
    )
    args = parser.parse_args(argv)

    scenario_ids = golden_manifest_scenario_ids(args.manifest)
    if not args.confirm_real_s3_writes:
        print("Refusing to run: pass --confirm-real-s3-writes to perform real S3 uploads/deletes.")
        print("The smoke uses isolated metadata keys and does not write productive projects_index.json.")
        return 2

    config = load_project_management_runtime_config(
        config_path=args.config or None,
        preview=not args.final_config,
    )
    if args.bucket:
        config = config.__class__(
            aws_access_key_id=config.aws_access_key_id,
            aws_secret_access_key=config.aws_secret_access_key,
            region_name=args.region or config.region_name,
            bucket_name=args.bucket,
        )
    elif args.region:
        config = config.__class__(
            aws_access_key_id=config.aws_access_key_id,
            aws_secret_access_key=config.aws_secret_access_key,
            region_name=args.region,
            bucket_name=config.bucket_name,
        )
    if not config.ready:
        print(f"Runtime config incomplete: {', '.join(config.missing_fields)}")
        return 1

    try:
        import boto3
    except ImportError as exc:
        print("boto3 is required to run the real S3 acceptance smoke.")
        raise SystemExit(1) from exc

    session = boto3.Session(
        aws_access_key_id=config.aws_access_key_id,
        aws_secret_access_key=config.aws_secret_access_key,
        region_name=config.region_name,
    )
    result = run_v2_s3_acceptance_smoke(
        s3_client=session.client("s3"),
        config=S3AcceptanceSmokeConfig(
            bucket_name=config.bucket_name,
            run_id=args.run_id,
            cleanup=not args.no_cleanup,
        ),
        scenario_ids=scenario_ids,
    )

    result_path = Path(args.result)
    result_path.parent.mkdir(parents=True, exist_ok=True)
    result_path.write_text(
        json.dumps(result.__dict__, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    print(f"S3 acceptance smoke result written: {result_path}")
    print(f"status: {result.status}")
    print(f"test_prefix: {result.test_prefix}")
    print(f"scenarios_passed: {len(result.scenarios_passed)}/{len(scenario_ids)}")

    if args.write_acceptance:
        acceptance_path = Path(args.acceptance)
        acceptance_data = load_acceptance_evidence(acceptance_path)
        if not acceptance_data:
            acceptance_data = build_acceptance_evidence_template(required_s3_scenarios=scenario_ids)
        acceptance_path.parent.mkdir(parents=True, exist_ok=True)
        acceptance_path.write_text(
            json.dumps(
                merge_s3_smoke_into_acceptance_evidence(acceptance_data, result),
                indent=2,
                ensure_ascii=False,
            )
            + "\n",
            encoding="utf-8",
            newline="\n",
        )
        print(f"Acceptance S3 gate updated: {acceptance_path}")

    return 0 if result.passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
