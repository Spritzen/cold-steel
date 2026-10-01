"""Finds Stellaris: Steam's library list, then the game folder, then the game's own
`launcher-settings.json`, which names the version and the user data folder.
See docs/reference/stellaris-files.md.
"""

import json
import os
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from cold_steel.paradox.vdf import VdfError, VdfValue, parse_vdf

STELLARIS_APP_ID = "281990"

# The normal (not Flatpak or Snap) Steam install. Others are set by hand.
DEFAULT_STEAM_DIRS = (Path("~/.local/share/Steam"), Path("~/.steam/steam"))


class GameNotFound(Exception):  # noqa: N818 (reads better at the call site)
    pass


@dataclass(frozen=True)
class Game:
    install_dir: Path  # steamapps/common/Stellaris
    library_dir: Path  # the Steam library holding it
    version: str  # "v4.5.1"
    version_name: str  # "Cygnus v4.5.1 (358e)"
    data_dir: Path  # Paradox user data: .../Paradox Interactive/Stellaris

    @property
    def workshop_dir(self) -> Path:
        return self.library_dir / "steamapps/workshop/content" / STELLARIS_APP_ID

    @property
    def mod_dir(self) -> Path:
        return self.data_dir / "mod"

    @property
    def launcher_db(self) -> Path:
        return self.data_dir / "launcher-v2.sqlite"


def find_game(steam_dirs: Iterable[Path]) -> Game:
    """Look in each Steam folder in turn. Raises `GameNotFound` with a reason."""
    tried: list[str] = []
    for steam_dir in steam_dirs:
        steam_dir = steam_dir.expanduser()
        if not (steam_dir / "steamapps").is_dir():
            tried.append(f"{steam_dir} (no Steam here)")
            continue
        for library in steam_libraries(steam_dir):
            install = library / "steamapps/common" / _install_folder(library)
            if (install / "launcher-settings.json").is_file():
                return _read_game(install, library)
        tried.append(f"{steam_dir} (Stellaris isn't installed in its libraries)")
    raise GameNotFound("Stellaris wasn't found. Looked in: " + "; ".join(tried or ["nowhere"]))


def steam_libraries(steam_dir: Path) -> list[Path]:
    """Every Steam library folder, the main one first."""
    libraries = [steam_dir]
    try:
        vdf = parse_vdf((steam_dir / "steamapps/libraryfolders.vdf").read_text("utf-8"))
    except OSError, UnicodeDecodeError, VdfError:
        return libraries
    folders = _lower_keys(vdf).get("libraryfolders", {})
    if isinstance(folders, dict):
        for value in folders.values():
            # New format: "0" { "path" "..." }. Old format: "1" "...".
            path = value.get("path") if isinstance(value, dict) else value
            if isinstance(path, str) and Path(path) not in libraries:
                libraries.append(Path(path))
    return libraries


def _install_folder(library: Path) -> str:
    """The folder name from the app manifest; "Stellaris" if there's no manifest."""
    manifest = library / f"steamapps/appmanifest_{STELLARIS_APP_ID}.acf"
    try:
        state = _lower_keys(parse_vdf(manifest.read_text("utf-8"))).get("appstate", {})
    except OSError, UnicodeDecodeError, VdfError:
        return "Stellaris"
    folder = _lower_keys(state).get("installdir") if isinstance(state, dict) else None
    return folder if isinstance(folder, str) and folder else "Stellaris"


def _lower_keys(value: VdfValue) -> dict[str, VdfValue]:
    # Valve's keys are case-insensitive ("AppState", "installdir").
    return {k.lower(): v for k, v in value.items()} if isinstance(value, dict) else {}


def _read_game(install: Path, library: Path) -> Game:
    settings_file = install / "launcher-settings.json"
    try:
        settings = json.loads(settings_file.read_text("utf-8-sig"))
        raw_version = str(settings["rawVersion"])
        data_path = str(settings["gameDataPath"])
    except (OSError, ValueError, KeyError, TypeError) as error:
        raise GameNotFound(f"Couldn't read {settings_file}: {error}") from error

    if "%USER_DOCUMENTS%" in data_path:
        raise GameNotFound(
            "This is the Windows build of Stellaris (Steam Play is turned on for it). "
            "Cold Steel needs the native Linux build."
        )
    data_dir = Path(data_path.replace("$LINUX_DATA_HOME", str(linux_data_home())))
    return Game(
        install_dir=install,
        library_dir=library,
        version=raw_version,
        version_name=str(settings.get("version", raw_version)),
        data_dir=data_dir,
    )


def linux_data_home() -> Path:
    """What the game means by `$LINUX_DATA_HOME`: the XDG data folder."""
    value = os.environ.get("XDG_DATA_HOME", "")
    return Path(value) if value and Path(value).is_absolute() else Path.home() / ".local/share"
