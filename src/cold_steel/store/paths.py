"""Where Cold Steel keeps its own files, following the XDG base directory spec."""

import os
from pathlib import Path

APP_NAME = "cold-steel"


def _xdg(var: str, default: str) -> Path:
    # The spec says to ignore the variable when it is empty or not absolute.
    value = os.environ.get(var, "")
    base = Path(value) if value and Path(value).is_absolute() else Path.home() / default
    return base / APP_NAME


def shown(path: Path) -> str:
    """A path as the user reads it: the home folder as `~`."""
    try:
        return f"~/{path.relative_to(Path.home())}"
    except ValueError:
        return str(path)


def config_dir() -> Path:
    """Settings. `~/.config/cold-steel/`."""
    return _xdg("XDG_CONFIG_HOME", ".config")


def data_dir() -> Path:
    """Playsets, conflict choices, patch mods, pinned copies, builds and backups.
    `~/.local/share/cold-steel/`."""
    return _xdg("XDG_DATA_HOME", ".local/share")


def cache_dir() -> Path:
    """Caches: mods, health results, the object index, thumbnails. Safe to delete.
    `~/.cache/cold-steel/`."""
    return _xdg("XDG_CACHE_HOME", ".cache")
