"""Backup or restore productive S3 metadata before legacy Golden captures."""

from __future__ import annotations

import argparse
from datetime import datetime
from pathlib import Path
import sys

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from dronautix_uploader.adapters.runtime_services import load_project_management_runtime_config
from dronautix_uploader.core.legacy_s3_metadata_backup import (
    backup_legacy_s3_metadata,
    restore_legacy_s3_metadata,
)


def main(argv: list[str] | None = None, *, session_factory=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    backup_parser = subparsers.add_parser("backup", help="Download projects_index.json and deleted_projects.json.")
    _add_runtime_args(backup_parser)
    backup_parser.add_argument(
        "--output",
        default="",
        help="Backup directory. Defaults to artifacts/s3-metadata-backup-<timestamp>.",
    )

    restore_parser = subparsers.add_parser("restore", help="Restore projects_index.json and deleted_projects.json.")
    _add_runtime_args(restore_parser)
    restore_parser.add_argument("backup_dir", help="Directory created by the backup command.")
    restore_parser.add_argument(
        "--restore-missing",
        action="store_true",
        help="Delete metadata keys that were missing when the backup was created.",
    )
    restore_parser.add_argument(
        "--confirm-restore-productive-metadata",
        action="store_true",
        help="Required. Confirms productive root metadata may be overwritten.",
    )
    args = parser.parse_args(argv)

    if args.command == "restore" and not args.confirm_restore_productive_metadata:
        print("Refusing to restore: pass --confirm-restore-productive-metadata.")
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

    s3_client = _create_s3_client(config, session_factory=session_factory)
    if args.command == "backup":
        output_dir = Path(args.output) if args.output else Path("artifacts") / f"s3-metadata-backup-{_timestamp()}"
        result = backup_legacy_s3_metadata(s3_client, output_dir, bucket_name=config.bucket_name)
        print(f"S3 metadata backup written: {result.backup_dir}")
        print(f"manifest: {result.manifest_path}")
        print(f"saved: {', '.join(result.saved_keys) or '-'}")
        print(f"missing: {', '.join(result.missing_keys) or '-'}")
        return 0

    result = restore_legacy_s3_metadata(
        s3_client,
        args.backup_dir,
        bucket_name=config.bucket_name,
        restore_missing=args.restore_missing,
    )
    print(f"S3 metadata restored from: {result.backup_dir}")
    print(f"restored: {', '.join(result.restored_keys) or '-'}")
    print(f"skipped missing: {', '.join(result.skipped_missing_keys) or '-'}")
    print(f"deleted missing: {', '.join(result.deleted_missing_keys) or '-'}")
    return 0


def _add_runtime_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--bucket", default="", help="Override bucket from config.")
    parser.add_argument("--region", default="", help="Override region from config.")
    parser.add_argument("--config", default="", help="Optional config.json path.")
    parser.add_argument("--final-config", action="store_true", help="Use final app config path instead of preview path.")


def _create_s3_client(config, *, session_factory=None):
    factory = session_factory
    if factory is None:
        try:
            import boto3
        except ImportError as exc:
            raise RuntimeError("boto3 is required for S3 metadata backup/restore.") from exc
        factory = boto3.Session
    session = factory(
        aws_access_key_id=config.aws_access_key_id,
        aws_secret_access_key=config.aws_secret_access_key,
        region_name=config.region_name,
    )
    return session.client("s3")


def _timestamp() -> str:
    return datetime.now().strftime("%Y%m%d-%H%M%S")


if __name__ == "__main__":
    raise SystemExit(main())
