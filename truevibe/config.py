from __future__ import annotations

import os
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = PROJECT_ROOT / "data"


def get_db_path() -> Path:
    """
    Resolve the SQLite file path, honoring an optional TRUEVIBE_DB_PATH override.
    """
    override = os.getenv("TRUEVIBE_DB_PATH")
    if override:
        return Path(override).expanduser().resolve()
    return DATA_DIR / "truevibe-db.db"
