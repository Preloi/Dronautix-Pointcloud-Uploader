"""Generate deterministic V2 raw outputs for Golden comparison staging.

Usage:
    python tools/generate_v2_golden_output.py
    python tools/generate_v2_golden_output.py --scenario single_copc_upload
"""

from __future__ import annotations

import argparse
from pathlib import Path
import sys

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from dronautix_uploader.core.v2_golden_output import (
    SUPPORTED_V2_GOLDEN_SCENARIOS,
    generate_v2_golden_outputs,
)


DEFAULT_MANIFEST = Path("tests/golden/manifest.json")
DEFAULT_OUTPUT_ROOT = Path("artifacts/v2-golden-generated")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--manifest",
        default=str(DEFAULT_MANIFEST),
        help="Path to the Golden manifest JSON.",
    )
    parser.add_argument(
        "--output-root",
        default=str(DEFAULT_OUTPUT_ROOT),
        help="Directory that receives generated V2 scenario folders.",
    )
    parser.add_argument(
        "--scenario",
        choices=SUPPORTED_V2_GOLDEN_SCENARIOS,
        default=None,
        help="Optional Golden scenario id. Defaults to all supported V2 scenarios.",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Replace an existing generated scenario output directory.",
    )
    args = parser.parse_args(argv)

    results = generate_v2_golden_outputs(
        args.manifest,
        output_root=args.output_root,
        scenario_id=args.scenario,
        overwrite=args.overwrite,
    )

    print(f"Generated V2 Golden output: {len(results)} scenario(s)")
    for result in results:
        print(f"[{result.scenario_id}] {result.output_dir}")
        for generated_file in result.generated_files:
            print(f"  file: {generated_file}")
        if result.uploaded_keys:
            print(f"  uploaded: {len(result.uploaded_keys)} object(s)")
    print("Next: python tools/import_v2_golden_output.py <scenario> --source-dir <generated-scenario-dir>")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
