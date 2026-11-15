"""Static application constants.

Values here are fixed at build time. User-supplied configuration is read
from the environment in app.main.
"""

from pathlib import Path

# Project directories
BASE_DIR = Path(__file__).resolve().parent.parent
ASSETS_DIR = BASE_DIR / "assets"

# General information
APP_AUTHOR = 't0mmili'
APP_NAME = 'Backblaze Prometheus Exporter'
APP_VERSION = '0.2.0'

# Assets
B2_BUCKETS_SCHEMA_FILE = ASSETS_DIR / 'bucket-path-mapping.schema.json'
FLASK_TEMPLATE_FOLDER = ASSETS_DIR

# Collector tuning
RETRY_BACKOFF_INITIAL_SECONDS = 5
RETRY_BACKOFF_MAX_SECONDS = 300
CYCLE_JITTER_FRACTION = 0.05
CYCLE_MIN_SLEEP_SECONDS = 1.0
SLOW_SCRAPE_THRESHOLD_SECONDS = 30
