"""Confine user-supplied server filesystem paths to allowed roots.

Several API endpoints accept server paths (directory ingestion, template
merge input/output). Unconfined, they let any API caller index and then
read back arbitrary server files, or write files anywhere the process can.

Config (env):
  SERVER_PATH_ROOTS     os.pathsep-separated allowed roots
                        (default: DATA_DIR and REPORTS_DIR)
  SERVER_PATH_RESTRICT  true|false for the UI's directory import; defaults to
                        true in containers/servers (RUNNING_IN_DOCKER or
                        /.dockerenv) and false for local desktop use, where
                        the user indexes their own folders.
"""

import os
from pathlib import Path
from typing import List, Union


class PathNotAllowed(ValueError):
    pass


def allowed_roots() -> List[Path]:
    raw = os.getenv("SERVER_PATH_ROOTS", "")
    if raw.strip():
        roots = [r for r in raw.split(os.pathsep) if r.strip()]
    else:
        try:
            from app.config import get_settings

            settings = get_settings()
            roots = [settings.DATA_DIR, settings.REPORTS_DIR]
        except Exception:
            roots = ["data", "reports"]
    return [Path(r).expanduser().resolve() for r in roots]


def restriction_enabled() -> bool:
    flag = os.getenv("SERVER_PATH_RESTRICT", "").strip().lower()
    if flag in ("1", "true", "yes", "on"):
        return True
    if flag in ("0", "false", "no", "off"):
        return False
    return bool(os.getenv("RUNNING_IN_DOCKER")) or os.path.exists("/.dockerenv")


def resolve_within_roots(path: Union[str, Path], *, enforce: bool = True) -> Path:
    """Resolve ``path`` (following symlinks) and require it under an allowed root.

    With ``enforce=False`` the path is only resolved (local desktop mode).
    """
    if not str(path or "").strip():
        raise PathNotAllowed("Path is required")
    resolved = Path(path).expanduser().resolve()
    if not enforce:
        return resolved
    for root in allowed_roots():
        if resolved == root or resolved.is_relative_to(root):
            return resolved
    raise PathNotAllowed(
        "Path is outside the allowed server directories; configure SERVER_PATH_ROOTS to permit it."
    )
