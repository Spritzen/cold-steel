"""Save files: what one `.sav` file says about itself, and where the game keeps
them. See docs/reference/stellaris-files.md.

A `.sav` is a zip holding `meta` (about 1 KB) and `gamestate` (megabytes).
Only `meta` is read:

    version="Cygnus v4.5.2"
    name="Commonwealth of Man"
    date="2203.01.08"
    required_dlcs={ "Apocalypse" "Federations" }
    mods={ "Cold Steel build: Cold Steel Mix" }
"""

import re
import zipfile
from pathlib import Path

import msgspec

from cold_steel.paradox.game import STELLARIS_APP_ID
from cold_steel.paradox.script import Node, ParseError, parse
from cold_steel.paradox.vdf import VdfError, parse_vdf

SAVE_FOLDER = "save games"
_CLOUD_SETTING = re.compile(rb'^[ \t]*autosave_tocloud[ \t]*=[ \t]*"?(\w+)"?', re.MULTILINE)
# A SteamID64 is this plus the account id, which names the user's userdata/ folder.
_STEAM_ID_BASE = 76561197960265728


class SaveError(Exception):
    pass


class SaveInfo(msgspec.Struct, frozen=True):
    empire: str  # "Commonwealth of Man"
    date: str  # the in-game date, "2203.01.08"
    version: str  # the game that wrote it, "Cygnus v4.5.2"
    mods: tuple[str, ...] = ()  # mod names, in load order
    dlcs: tuple[str, ...] = ()  # the DLC the save needs, by name, as dlc.py has them


def read_save_info(path: Path) -> SaveInfo:
    """Read a save's `meta`. Raises `SaveError` if it isn't a readable save."""
    try:
        with zipfile.ZipFile(path) as zf:
            data = zf.read("meta")
    except KeyError as error:
        raise SaveError("No meta in the save") from error
    except (OSError, zipfile.BadZipFile) as error:
        raise SaveError(f"Not a readable save: {error}") from error
    try:
        nodes = parse(data.decode("utf-8-sig", errors="replace"))
    except ParseError as error:
        raise SaveError(f"Its meta is broken: {error}") from error

    text: dict[str, str] = {}
    lists: dict[str, tuple[str, ...]] = {}
    for node in nodes:
        if isinstance(node.value, str) and node.key:
            text[node.key] = node.value
        elif node.key in ("mods", "required_dlcs") and isinstance(node.value, tuple):
            lists[node.key] = _strings(node.value)
    return SaveInfo(
        empire=text.get("name", ""),
        date=text.get("date", ""),
        version=text.get("version", ""),
        mods=lists.get("mods", ()),
        dlcs=lists.get("required_dlcs", ()),
    )


def _strings(nodes: tuple[Node, ...]) -> tuple[str, ...]:
    return tuple(n.value for n in nodes if n.key is None and isinstance(n.value, str))


def local_save_dir(data_dir: Path) -> Path:
    """Where the game writes saves: `<Paradox data>/save games/`."""
    return data_dir / SAVE_FOLDER


def autosaves_to_cloud(data_dir: Path) -> bool | None:
    """Whether the game sends autosaves to Steam Cloud: `autosave_tocloud` in
    its `settings.txt`, the game's "Autosave to Cloud". None when `settings.txt`
    is missing or can't be read.

    A missing line counts as yes. The game drops the line when Steam Cloud is
    off for it in Steam, and where its autosaves go then isn't known.
    """
    try:
        found = _CLOUD_SETTING.search((data_dir / "settings.txt").read_bytes())
    except OSError:
        return None
    return found is None or found.group(1) != b"no"


def cloud_save_dirs(steam_dir: Path) -> tuple[Path, ...]:
    """Steam Cloud's copy of the saves, where autosaves go by default:
    `<Steam>/userdata/<account id>/281990/remote/save games/`.

    Only the Steam user who logged in last, when `loginusers.vdf` says who that
    is and they have the folder. Otherwise every user's folder.
    """
    every = sorted((steam_dir / "userdata").glob(f"*/{STELLARIS_APP_ID}/remote/{SAVE_FOLDER}"))
    user = _last_steam_user(steam_dir)
    mine = steam_dir / "userdata" / user / STELLARIS_APP_ID / "remote" / SAVE_FOLDER
    return (mine,) if user and mine in every else tuple(every)


def _last_steam_user(steam_dir: Path) -> str:
    """The account id of the Steam user who logged in last, or ""."""
    try:
        users = parse_vdf((steam_dir / "config/loginusers.vdf").read_text("utf-8"))
    except OSError, UnicodeDecodeError, VdfError:
        return ""
    found = next((v for k, v in users.items() if k.lower() == "users"), None)
    if not isinstance(found, dict):
        return ""

    def rank(item: tuple[str, object]) -> tuple[bool, int]:
        info = item[1]
        if not isinstance(info, dict):
            return False, 0
        fields = {k.lower(): v for k, v in info.items()}
        stamp = fields.get("timestamp", "0")
        return fields.get("mostrecent") == "1", int(stamp) if str(stamp).isdigit() else 0

    candidates = [(k, v) for k, v in found.items() if k.isdigit() and isinstance(v, dict)]
    if not candidates:
        return ""
    steam_id = int(max(candidates, key=rank)[0])
    return str(steam_id - _STEAM_ID_BASE) if steam_id > _STEAM_ID_BASE else ""
