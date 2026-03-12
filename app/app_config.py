from pathlib import Path

# Project directories
BASE_DIR = Path(__file__).resolve().parent.parent
ASSETS_DIR = BASE_DIR / "assets"

# General information
APP_AUTHOR = 't0mmili'
APP_NAME = 'Backblaze Prometheus Exporter'
APP_VERSION = '0.1.0'
B2_SDK_VERSION = 'v3'

# Assets
B2_BUCKETS_SCHEMA_FILE = ASSETS_DIR / 'bucket-path-mapping.schema.json'
FLASK_TEMPLATE_FOLDER = ASSETS_DIR
