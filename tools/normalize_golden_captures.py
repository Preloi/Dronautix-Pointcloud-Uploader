"""Normalize captured legacy Golden Master fixtures.

Usage:
    python tools/normalize_golden_captures.py
    python tools/normalize_golden_captures.py --scenario single_copc_upload
"""

from __future__ import annotations

import argparse
from pathlib import Path
import sys

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from dronautix_uploader.core.golden_capture import normalize_captured_golden_files


DEFAULT_MANIFEST = Path("tests/golden/manifest.json")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--manifest",
        default=str(DEFAULT_MANIFEST),
        help="Path to the Golden manifest JSON.",
    )
    parser.add_argument(
        "--scenario",
        default=None,
        help="Optional scenario id to normalize instead of all captured scenarios.",
    )
    args = parser.parse_args()

    results = normalize_captured_golden_files(args.manifest, scenario_id=args.scenario)
    for result in results:
        print(f"{result.scenario_id}: {result.status} - {result.message}")
        for file_result in result.files:
            print(f"  {file_result.raw_path} -> {file_result.normalized_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
