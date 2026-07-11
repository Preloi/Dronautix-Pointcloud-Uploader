"""Shared constants for the UI-free V2 core."""

APPDATA_FOLDER = "DronautixUploader"
KEYRING_SERVICE = "DronautixUploader"

BUCKET_NAME = "potreedronautix"
REGION_NAME = "eu-central-1"
DOMAIN_URL = "https://pointcloud.dronautix.at/index.html"

S3_INDEX_JSON = "projects_index.json"
S3_DELETED_JSON = "deleted_projects.json"
S3_DISABLED_PROJECTS_KEY = "disabled_projects"
PROJECT_LINK_DISABLED_UI_KEY = "_link_disabled"
S3_DELETE_BATCH_SIZE = 1000
DELETED_PROJECT_RETENTION_DAYS = 30

BUNDLED_CONVERTER_DIR = ("bundled_tools", "PotreeConverter")
BUNDLED_CONVERTER_EXE = "PotreeConverter.exe"
BUNDLED_CONVERTER_DLL = "laszip.dll"

UPDATE_REPO_OWNER = "Preloi"
UPDATE_REPO_NAME = "Dronautix-Pointcloud-Uploader"
UPDATE_MANIFEST_BRANCH = "master"
UPDATE_MANIFEST_URL = (
    "https://raw.githubusercontent.com/"
    f"{UPDATE_REPO_OWNER}/{UPDATE_REPO_NAME}/"
    f"{UPDATE_MANIFEST_BRANCH}/latest-release.json"
)

S3_CACHE_CONTROL = "no-cache, no-store, must-revalidate, max-age=0"
S3_INDEX_CACHE_CONTROL = "no-cache"
S3_DELETED_CACHE_CONTROL = "no-cache, no-store, must-revalidate"
COPC_OBJECT_NAME = "source.copc.laz"
