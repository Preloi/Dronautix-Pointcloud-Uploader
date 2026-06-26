"""Compare V2 output files against normalized legacy Golden snapshots."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from dronautix_uploader.core.golden_capture import compare_golden_outputs


DEFAULT_MANIFEST = Path("tests/golden/manifest.json")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--manifest",
        default=str(DEFAULT_MANIFEST),
        help="Path to the Golden manifest JSON.",
    )
    parser.add_argument(
        "--actual-root",
        default=None,
        help="Root containing V2 outputs by scenario. Defaults to manifest v2_output_root.",
    )
    parser.add_argument(
        "--scenario",
        default=None,
        help="Optional scenario id to compare instead of all scenarios.",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Return non-zero until every compared file matches.",
    )
    args = parser.parse_args(argv)

    report = compare_golden_outputs(
        args.manifest,
        actual_root=args.actual_root,
        scenario_id=args.scenario,
    )
    print(f"V2 vs Golden comparison: {report.ready_count}/{report.scenario_count} scenarios match")
    for scenario in report.scenarios:
        status = "MATCH" if scenario.ready else "BLOCKED"
        print(f"[{status}] {scenario.scenario_id}")
        print(f"  actual: {scenario.actual_dir}")
        print(f"  expected: {scenario.expected_dir}")
        for file_result in scenario.files:
            print(f"  [{file_result.status.upper()}] {file_result.file_name}: {file_result.detail}")

    if args.strict and not report.ready:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
