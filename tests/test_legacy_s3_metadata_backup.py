import io
import json

from dronautix_uploader.core.constants import S3_DELETED_CACHE_CONTROL, S3_INDEX_CACHE_CONTROL
from dronautix_uploader.core.legacy_s3_metadata_backup import (
    BACKUP_MANIFEST_NAME,
    backup_legacy_s3_metadata,
    restore_legacy_s3_metadata,
)
from tools.backup_legacy_s3_metadata import main as backup_legacy_s3_metadata_cli


def test_backup_legacy_s3_metadata_downloads_root_metadata_json(tmp_path):
    fake_s3 = FakeS3Client(
        {
            "projects_index.json": b'{"projects":[]}',
            "deleted_projects.json": b'{"deleted_projects":[]}',
        }
    )

    result = backup_legacy_s3_metadata(
        fake_s3,
        tmp_path / "backup",
        bucket_name="bucket",
        now_utc="2026-06-21T00:00:00Z",
    )

    assert result.saved_keys == ("projects_index.json", "deleted_projects.json")
    assert result.missing_keys == ()
    assert (tmp_path / "backup" / "projects_index.json").read_text(encoding="utf-8") == '{"projects":[]}'
    manifest = json.loads((tmp_path / "backup" / BACKUP_MANIFEST_NAME).read_text(encoding="utf-8"))
    assert manifest["bucket_name"] == "bucket"
    assert manifest["created_at_utc"] == "2026-06-21T00:00:00Z"
    assert fake_s3.get_calls == [
        ("bucket", "projects_index.json"),
        ("bucket", "deleted_projects.json"),
    ]


def test_backup_legacy_s3_metadata_records_missing_deleted_projects(tmp_path):
    fake_s3 = FakeS3Client({"projects_index.json": b'{"projects":[]}'})

    result = backup_legacy_s3_metadata(fake_s3, tmp_path / "backup", bucket_name="bucket")

    assert result.saved_keys == ("projects_index.json",)
    assert result.missing_keys == ("deleted_projects.json",)
    manifest = json.loads((tmp_path / "backup" / BACKUP_MANIFEST_NAME).read_text(encoding="utf-8"))
    deleted_entry = next(item for item in manifest["objects"] if item["key"] == "deleted_projects.json")
    assert deleted_entry["status"] == "missing"


def test_restore_legacy_s3_metadata_puts_json_with_legacy_cache_control(tmp_path):
    backup_dir = tmp_path / "backup"
    backup_dir.mkdir()
    (backup_dir / "projects_index.json").write_text('{"projects":[]}', encoding="utf-8")
    (backup_dir / "deleted_projects.json").write_text('{"deleted_projects":[]}', encoding="utf-8")
    (backup_dir / BACKUP_MANIFEST_NAME).write_text(
        json.dumps(
            {
                "schema_version": 1,
                "bucket_name": "backup-bucket",
                "objects": [
                    {
                        "key": "projects_index.json",
                        "status": "saved",
                        "path": "projects_index.json",
                        "size_bytes": 15,
                    },
                    {
                        "key": "deleted_projects.json",
                        "status": "saved",
                        "path": "deleted_projects.json",
                        "size_bytes": 23,
                    },
                ],
            }
        ),
        encoding="utf-8",
    )
    fake_s3 = FakeS3Client()

    result = restore_legacy_s3_metadata(fake_s3, backup_dir)

    assert result.bucket_name == "backup-bucket"
    assert result.restored_keys == ("projects_index.json", "deleted_projects.json")
    assert fake_s3.puts[0]["CacheControl"] == S3_INDEX_CACHE_CONTROL
    assert fake_s3.puts[1]["CacheControl"] == S3_DELETED_CACHE_CONTROL
    assert fake_s3.objects["projects_index.json"] == b'{"projects":[]}'


def test_restore_legacy_s3_metadata_can_delete_keys_that_were_missing(tmp_path):
    backup_dir = tmp_path / "backup"
    backup_dir.mkdir()
    (backup_dir / BACKUP_MANIFEST_NAME).write_text(
        json.dumps(
            {
                "schema_version": 1,
                "bucket_name": "bucket",
                "objects": [{"key": "deleted_projects.json", "status": "missing"}],
            }
        ),
        encoding="utf-8",
    )
    fake_s3 = FakeS3Client({"deleted_projects.json": b'{"deleted_projects":[]}'})

    result = restore_legacy_s3_metadata(fake_s3, backup_dir, restore_missing=True)

    assert result.deleted_missing_keys == ("deleted_projects.json",)
    assert fake_s3.deleted == [("bucket", "deleted_projects.json")]
    assert "deleted_projects.json" not in fake_s3.objects


def test_backup_legacy_s3_metadata_cli_refuses_restore_without_confirmation(tmp_path, capsys):
    backup_dir = tmp_path / "backup"
    backup_dir.mkdir()

    exit_code = backup_legacy_s3_metadata_cli(["restore", str(backup_dir)])

    captured = capsys.readouterr()
    assert exit_code == 2
    assert "Refusing to restore" in captured.out


class FakeS3Client:
    class exceptions:
        class NoSuchKey(Exception):
            pass

    def __init__(self, objects=None):
        self.objects = dict(objects or {})
        self.get_calls = []
        self.puts = []
        self.deleted = []

    def get_object(self, Bucket, Key):
        self.get_calls.append((Bucket, Key))
        if Key not in self.objects:
            raise self.exceptions.NoSuchKey(Key)
        return {"Body": io.BytesIO(self.objects[Key])}

    def put_object(self, **kwargs):
        self.puts.append(kwargs)
        body = kwargs.get("Body", b"")
        self.objects[kwargs["Key"]] = body if isinstance(body, bytes) else str(body).encode("utf-8")

    def delete_objects(self, Bucket, Delete):
        for item in Delete.get("Objects", []):
            key = item["Key"]
            self.deleted.append((Bucket, key))
            self.objects.pop(key, None)
        return {"Deleted": Delete.get("Objects", [])}
