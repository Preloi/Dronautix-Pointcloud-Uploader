"""Initialize a legacy Golden Master capture directory and provenance file."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
from pathlib import Path
import sys

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from dronautix_uploader.core.golden_capture import golden_manifest_scenario_ids, initialize_golden_capture_scenario


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
        help="Initialize every Golden scenario from the manifest.",
    )
    parser.add_argument(
        "--manifest",
        default=str(DEFAULT_MANIFEST),
        help="Path to the Golden manifest JSON.",
    )
    parser.add_argument(
        "--legacy-app-version",
        default="",
        help="Legacy app version that produced the capture.",
    )
    parser.add_argument(
        "--legacy-git-ref",
        default="",
        help="Legacy git ref or commit that produced the capture.",
    )
    parser.add_argument(
        "--captured-at-utc",
        default="",
        help="Capture timestamp. Defaults to the current UTC timestamp.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Overwrite an existing provenance file.",
    )
    args = parser.parse_args(argv)
    if args.all and args.scenario:
        parser.error("Use either a scenario id or --all, not both.")
    if not args.all and not args.scenario:
        parser.error("Pass a scenario id or --all.")

    captured_at_utc = args.captured_at_utc or _utc_timestamp()
    scenario_ids = golden_manifest_scenario_ids(args.manifest) if args.all else (args.scenario,)
    results = tuple(
        initialize_golden_capture_scenario(
            args.manifest,
            scenario_id=scenario_id,
            legacy_app_version=args.legacy_app_version,
            legacy_git_ref=args.legacy_git_ref,
            captured_at_utc=captured_at_utc,
            overwrite_provenance=args.force,
        )
        for scenario_id in scenario_ids
    )

    if args.all:
        written_count = sum(1 for result in results if result.written)
        print(f"Initialized Golden captures: {len(results)} scenarios ({written_count} provenance written)")
    for result in results:
        print(f"Initialized Golden capture: {result.scenario_id}")
        print(f"  capture: {result.captured_dir}")
        print(f"  normalized: {result.normalized_dir}")
        if result.provenance_path is not None:
            action = "written" if result.written else "kept"
            print(f"  provenance ({action}): {result.provenance_path}")
    return 0


def _utc_timestamp() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


if __name__ == "__main__":
    raise SystemExit(main())
