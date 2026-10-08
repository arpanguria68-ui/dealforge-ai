"""Filesystem locations, resolved per machine instead of hard-coded.

Several modules used absolute Windows paths from the original developer's
machine (F:\\code project\\...). On Linux/Docker those resolve to a relative
directory literally named "F:\\code project\\..." in the working directory,
and the knowledge-base/template folders were never found.

Env overrides:
  OFAS_OUTPUT_DIR        generated model/memo/deck files (default $DATA_DIR/ofas_outputs)
  KNOWLEDGE_BASE_DIRS    os.pathsep-separated knowledge folders
  EXCEL_TEMPLATE_DIR     Excel model templates
"""

import os
from pathlib import Path
from typing import List

# backend/app/core/paths.py -> repository root
REPO_ROOT = Path(__file__).resolve().parents[3]
_KNOWLEDGE_ROOT = REPO_ROOT / "Knowledge managerment"


def _data_dir() -> Path:
    raw = os.getenv("DATA_DIR")
    if not raw:
        try:
            from app.config import get_settings

            raw = get_settings().DATA_DIR
        except Exception:
            raw = "data"
    return Path(raw).expanduser().resolve()


def output_dir() -> Path:
    return Path(os.getenv("OFAS_OUTPUT_DIR") or _data_dir() / "ofas_outputs").expanduser().resolve()


def knowledge_dirs() -> List[str]:
    raw = os.getenv("KNOWLEDGE_BASE_DIRS", "")
    if raw.strip():
        return [p for p in raw.split(os.pathsep) if p.strip()]
    return [str(_KNOWLEDGE_ROOT / "Excel knowledge"), str(_KNOWLEDGE_ROOT / "Finance knowledge base")]


def excel_template_dir() -> Path:
    return Path(os.getenv("EXCEL_TEMPLATE_DIR") or _KNOWLEDGE_ROOT / "Excel knowledge").expanduser()
