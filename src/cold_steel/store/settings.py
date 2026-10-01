"""The user's settings, saved as `~/.config/cold-steel/settings.json`."""

from pathlib import Path

import msgspec

from cold_steel.store import paths
from cold_steel.store.files import load_json, save_json


class Settings(msgspec.Struct):
    # Set by hand when Steam isn't in the usual place (Flatpak, Snap, other disk).
    steam_dir: str = ""


def settings_file() -> Path:
    return paths.config_dir() / "settings.json"


def load_settings(path: Path | None = None) -> Settings:
    return load_json(path or settings_file(), Settings) or Settings()


def save_settings(settings: Settings, path: Path | None = None) -> None:
    save_json(path or settings_file(), settings)
