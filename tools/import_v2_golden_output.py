"""Import raw V2 scenario output files for Golden comparison.

Usage:
    python tools/import_v2_golden_output.py multi_replace --source-dir artifacts/v2-multi-replace
"""

from __future__ import annotations

import argparse
from pathlib import Path
import sys

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from dronautix_uploader.core.golden_capture import import_v2_golden_output_files


DEFAULT_MANIFEST = Path("tests/golden/manifest.json")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "scenario",
        help="Golden scenario id from tests/golden/manifest.json, for example multi_replace.",
    )
    parser.add_argument(
        "--source-dir",
        required=True,
        help="Directory containing the raw V2 output files for this scenario.",
    )
    parser.add_argument(
        "--manifest",
        default=str(DEFAULT_MANIFEST),
        help="Path to the Golden manifest JSON.",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Replace already imported raw V2 output files for the scenario.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Alias for --overwrite.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate and print the planned import without writing files.",
    )
    args = parser.parse_args(argv)

    result = import_v2_golden_output_files(
        args.manifest,
        scenario_id=args.scenario,
        source_dir=args.source_dir,
        overwrite=args.overwrite or args.force,
        dry_run=args.dry_run,
    )

    action = "Planned V2 Golden output import" if result.dry_run else "Imported V2 Golden output"
    print(f"{action}: {result.scenario_id}")
    print(f"  source: {result.source_dir}")
    print(f"  output: {result.output_dir}")
    for copied_file in result.copied_files:
        print(f"  {copied_file.source_path} -> {copied_file.output_path}")
    if result.dry_run:
        print("  no files written")
    else:
        print(f"Next: python tools/compare_v2_to_golden.py --scenario {result.scenario_id} --strict")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
