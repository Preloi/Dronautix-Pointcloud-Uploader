"""Print the file-level plan for pending legacy Golden Master captures."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from dronautix_uploader.core.golden_capture import build_golden_capture_plan


DEFAULT_MANIFEST = Path("tests/golden/manifest.json")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--manifest",
        default=str(DEFAULT_MANIFEST),
        help="Path to the Golden manifest JSON.",
    )
    parser.add_argument(
        "--scenario",
        default=None,
        help="Optional scenario id to print instead of all scenarios.",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Return non-zero until every scenario is ready.",
    )
    args = parser.parse_args(argv)

    plan = build_golden_capture_plan(args.manifest, scenario_id=args.scenario)
    print(f"Golden capture plan: {plan.ready_count}/{plan.scenario_count} ready")
    for scenario in plan.scenarios:
        print(f"[{scenario.status.upper()}] {scenario.scenario_id}")
        if scenario.description:
            print(f"  {scenario.description}")
        print(f"  capture: {scenario.captured_dir}")
        print(f"  normalized: {scenario.normalized_dir}")
        print(f"  required: {', '.join(scenario.required_files)}")
        if scenario.provenance_file:
            print(f"  provenance: {scenario.captured_dir / scenario.provenance_file}")
        if scenario.missing_raw_files:
            print(f"  missing raw: {', '.join(scenario.missing_raw_files)}")
        if scenario.missing_normalized_files:
            print(f"  missing normalized: {', '.join(scenario.missing_normalized_files)}")
        if scenario.drifted_files:
            print(f"  drift: {', '.join(scenario.drifted_files)}")
        if scenario.missing_provenance:
            print("  missing provenance: yes")
        if scenario.provenance_issues:
            print(f"  provenance issues: {'; '.join(scenario.provenance_issues)}")
        next_steps = _scenario_next_steps(scenario, manifest_path=args.manifest)
        for index, step in enumerate(next_steps):
            label = "next" if index == 0 else "then"
            print(f"  {label}: {step}")

    if args.strict and not plan.ready:
        return 1
    return 0


def _scenario_next_steps(scenario, *, manifest_path: str) -> tuple[str, ...]:
    manifest_arg = f"--manifest {manifest_path}"
    if scenario.status == "pending":
        return (
            (
                f"python tools/init_golden_capture.py {scenario.scenario_id} {manifest_arg} "
                "--legacy-app-version <version> --legacy-git-ref <ref>"
            ),
        )
    if scenario.status == "incomplete":
        return (
            f"python tools/import_golden_capture.py {scenario.scenario_id} --source-dir <legacy-output-dir> {manifest_arg}",
        )
    if scenario.status in {"needs_normalization", "drift"}:
        return (f"python tools/normalize_golden_captures.py --scenario {scenario.scenario_id} {manifest_arg}",)
    if scenario.status in {"missing_provenance", "invalid_provenance"}:
        if scenario.provenance_file:
            return (f"fill or fix {scenario.captured_dir / scenario.provenance_file}",)
        return ()
    if scenario.status == "ready":
        return (
            f"python tools/import_v2_golden_output.py {scenario.scenario_id} --source-dir <v2-output-dir> {manifest_arg}",
            f"python tools/compare_v2_to_golden.py --scenario {scenario.scenario_id} {manifest_arg} --strict",
        )
    return ()


if __name__ == "__main__":
    raise SystemExit(main())
