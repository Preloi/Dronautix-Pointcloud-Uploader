"""Regenerate tests/snapshots after an *intended* change to the viewer output.

    python tools/update_output_snapshots.py            # show what would change
    python tools/update_output_snapshots.py --write    # write the new snapshots

The resulting git diff is the review of the viewer-contract change.
"""

from __future__ import annotations

import argparse
from pathlib import Path
import shutil
import sys
import tempfile

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from dronautix_uploader.core.output_snapshots import (  # noqa: E402
    SNAPSHOT_SCENARIOS,
    compare_with_snapshots,
    generate_output_snapshots,
    snapshot_file_names,
)

SNAPSHOT_ROOT = REPO_ROOT / "tests" / "snapshots"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--write", action="store_true", help="Overwrite tests/snapshots with the new output.")
    args = parser.parse_args(argv)

    with tempfile.TemporaryDirectory(prefix="dronautix_snapshots_") as directory:
        generated = Path(directory)
        generate_output_snapshots(generated)
        differences = compare_with_snapshots(generated, SNAPSHOT_ROOT)
        if not differences:
            print("[OK] Snapshots sind aktuell.")
            return 0
        for difference in differences:
            print(f"  - {difference}")
        if not args.write:
            print("Mit --write übernehmen (danach den git diff prüfen).")
            return 1
        for scenario_id in SNAPSHOT_SCENARIOS:
            target = SNAPSHOT_ROOT / scenario_id
            target.mkdir(parents=True, exist_ok=True)
            for name in snapshot_file_names(scenario_id):
                shutil.copyfile(generated / scenario_id / name, target / name)
        print(f"[OK] {len(differences)} Datei(en) aktualisiert; bitte git diff prüfen.")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
