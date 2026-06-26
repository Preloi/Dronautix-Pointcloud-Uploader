"""Import raw legacy Golden Master output files from a staging directory.

Usage:
    python tools/import_golden_capture.py multi_replace --source-dir artifacts/legacy-multi-replace
"""

from __future__ import annotations

import argparse
from pathlib import Path
import sys

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from dronautix_uploader.core.golden_capture import golden_manifest_scenario_ids, import_golden_capture_files


DEFAULT_MANIFEST = Path("tests/golden/manifest.json")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "scenario",
        nargs="?",
        help="Golden scenario id from tests/golden/manifest.json, for example multi_replace.",
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="Import every scenario from --source-root/<scenario-id>/.",
    )
    parser.add_argument(
        "--source-dir",
        default="",
        help="Directory containing the raw legacy output files for this scenario.",
    )
    parser.add_argument(
        "--source-root",
        default="",
        help="Root containing one raw legacy output directory per scenario. Required with --all.",
    )
    parser.add_argument(
        "--manifest",
        default=str(DEFAULT_MANIFEST),
        help="Path to the Golden manifest JSON.",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Replace already imported raw files for the scenario.",
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
    if args.all and args.scenario:
        parser.error("Use either a scenario id or --all, not both.")
    if args.all and not args.source_root:
        parser.error("--all requires --source-root.")
    if not args.all and (not args.scenario or not args.source_dir):
        parser.error("Pass a scenario id with --source-dir, or use --all with --source-root.")

    if args.all:
        scenario_imports = tuple(
            (scenario_id, Path(args.source_root) / scenario_id)
            for scenario_id in golden_manifest_scenario_ids(args.manifest)
        )
    else:
        scenario_imports = ((args.scenario, Path(args.source_dir)),)

    results = tuple(
        import_golden_capture_files(
            args.manifest,
            scenario_id=scenario_id,
            source_dir=source_dir,
            overwrite=args.overwrite or args.force,
            dry_run=args.dry_run,
        )
        for scenario_id, source_dir in scenario_imports
    )

    if args.all:
        action = "Planned Golden capture imports" if args.dry_run else "Imported Golden captures"
        print(f"{action}: {len(results)} scenarios")
    for result in results:
        action = "Planned Golden capture import" if result.dry_run else "Imported Golden capture"
        print(f"{action}: {result.scenario_id}")
        print(f"  source: {result.source_dir}")
        print(f"  capture: {result.captured_dir}")
        for copied_file in result.copied_files:
            print(f"  {copied_file.source_path} -> {copied_file.captured_path}")
        if result.dry_run:
            print("  no files written")
        else:
            next_command = "python tools/normalize_golden_captures.py"
            if not args.all:
                next_command += f" --scenario {result.scenario_id}"
            print(f"Next: {next_command}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
