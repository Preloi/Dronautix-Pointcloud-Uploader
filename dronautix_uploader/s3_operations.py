"""S3 operation boundary for the uploader package."""

__all__ = [
    "create_s3_client",
    "upload_files_to_s3",
    "delete_s3_objects",
    "collect_project_objects",
    "load_projects_index",
    "save_projects_index",
    "load_deleted_projects",
    "save_deleted_projects",
]


def __getattr__(name):
    if name in __all__:
        from . import main
        return getattr(main, name)
    raise AttributeError(name)
