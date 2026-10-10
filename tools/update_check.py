"""After Stellaris, its launcher, Steam or a mod updates: does Cold Steel still
understand every file it reads and writes?

    python3 tools/update_check.py            # what changed, and what's wrong now
    python3 tools/update_check.py --notes    # also Steam's patch notes, by topic
    python3 tools/update_check.py --accept   # make what's there now the next baseline

The check compares the files Cold Steel relies on with the baseline: how they
looked at the last accepted check. It reports two things. Problems are wrong
now, whatever the baseline says: a launcher column Cold Steel needs is gone,
or a key in dlc_load.json that Play would drop. Changes are differences from
the baseline: a new game folder, a new empire file, a mod Steam updated. Each
names what in Cold Steel reads it.

It only reads, so it's safe while the game or the launcher is open. The
baseline and the patch notes are kept in ~/.local/share/cold-steel/update-check/.
"""

import argparse
import json
import os
import re
import sqlite3
import sys
import urllib.request
import zipfile
from collections.abc import Iterable
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path

import msgspec

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from cold_steel import __version__
from cold_steel.core.build import builds_dir
from cold_steel.core.merge_rules import (
    GRAPHICS_FOLDERS,
    LOCALISATION_FOLDERS,
    SCRIPT_FOLDERS,
    load_rules,
)
from cold_steel.paradox.continue_game import FILE_NAME as CONTINUE_FILE
from cold_steel.paradox.descriptor import decode_descriptor
from cold_steel.paradox.dlc import find_dlcs, launcher_dlc_folder
from cold_steel.paradox.dlc_load import FILE_NAME as DLC_LOAD_FILE
from cold_steel.paradox.dlc_load import DlcLoad
from cold_steel.paradox.empires import (
    EMPIRE_GLOB,
    empire_file,
    file_format,
    join_empires,
    split_empires,
)
from cold_steel.paradox.error_log import read_error_log
from cold_steel.paradox.game import (
    DEFAULT_STEAM_DIRS,
    STELLARIS_APP_ID,
    Game,
    GameNotFound,
    find_game,
)
from cold_steel.paradox.launcher_db import REQUIRED_COLUMNS
from cold_steel.paradox.processes import GAME, LAUNCHER
from cold_steel.paradox.save import SAVE_FOLDER, cloud_save_dirs
from cold_steel.paradox.script import scan
from cold_steel.paradox.vdf import VdfError, VdfValue, parse_vdf
from cold_steel.store import paths
from cold_steel.store.empires import ListsFile, empires_dir, lists_file
from cold_steel.store.files import load_json, save_json
from cold_steel.store.settings import load_settings

# What Cold Steel writes to the launcher's tables. Any other column the launcher
# requires, with no default, makes Export and Sync launcher fail.
WRITTEN_COLUMNS = {
    "playsets": {"id", "name", "isActive", "createdOn", "loadOrder", "updatedOn", "isRemoved"},
    "playsets_mods": {"playsetId", "modId", "enabled", "position"},
    "playsets_dlcs": {"playsetId", "dlcId", "enabled"},
}
DLC_LOAD_KEYS = set(DlcLoad.__struct_fields__)
CONTINUE_KEYS = {"title", "desc", "date"}  # what Play and Continue write
SAVE_META_KEYS = {"name", "date", "version"}  # what Saves can't do without

# What each fact means to Cold Steel, so a change says where to look. Matched by prefix.
READERS = {
    "game version": "Outdated mods (core/version.py), save marks, and each empire list's last game",
    "mod compatibility version": "core/version.py: which mods are outdated",
    "game build": "Nothing reads it. Steam's build id changes with every game update, "
    "hotfixes included",
    "how the game starts": "Play (core/play.py) starts this program with these arguments",
    "game data path": "paradox/game.py: where every Paradox file below lives",
    "launcher-settings.json keys": "paradox/game.py reads rawVersion, version, gameDataPath, "
    "exePath and exeArgs",
    "launcher installer": "Nothing reads it. It's the Paradox launcher's version, and a new "
    "one may change the launcher tables below",
    "launcher migration": "Nothing reads it. It's the launcher database's newest layout "
    "change: check the launcher tables below",
    "launcher table": "paradox/launcher_db.py: import, Export, Open in launcher, Sync launcher",
    "dlc_load.json keys": "Play rewrites dlc_load.json with only enabled_mods and disabled_dlcs",
    "continue_game.json keys": "Play and Continue rewrite continue_game.json with title, desc "
    "and date",
    "settings.txt keys": "language= picks the localisation Conflicts read; autosave_tocloud= "
    "turns Saves and Empires on or off",
    "empire file": "Empires: each list records its format, and Play refuses a list in another "
    "(decision 112)",
    "empire fields": "Empires: paradox/empires.py reads origin, ethics, civics, authority and "
    "species to find the mods an empire needs",
    "save meta keys": "Saves: paradox/save.py reads name, date, version, mods and required_dlcs",
    "descriptor keys": "paradox/descriptor.py, and the .mod files Play adds",
    "playset backup keys": "Share (core/share.py) reads and writes this shape",
    "game folders": "Conflicts and the patch mod: merge_rules.json gives each folder its "
    "winner rule. A new folder uses the default unless a row says otherwise",
    "DLC folders": "paradox/dlc.py: the DLC window, dlc_load.json and the launcher's DLC ids",
    "game languages": "Conflicts read localisation only in the game's language",
}

type Value = str | list[str]
type Facts = dict[str, Value]


class Baseline(msgspec.Struct):
    accepted: str  # when, as an ISO time
    cold_steel: str  # the version that accepted it
    facts: Facts
    workshop: dict[str, int]  # Workshop id -> Steam's update time


@dataclass(frozen=True)
class Problem:
    area: str
    text: str


def check_dir() -> Path:
    return paths.data_dir() / "update-check"


def baseline_file() -> Path:
    return check_dir() / "baseline.json"


def notes_dir() -> Path:
    return check_dir() / "notes"


# --- What's there now ------------------------------------------------------


def collect(game: Game) -> Facts:
    """Every fact the baseline keeps, as the files say now."""
    facts: Facts = {}
    settings = _json(game.install_dir / "launcher-settings.json")
    facts["game version"] = game.version
    facts["mod compatibility version"] = str(settings.get("modsCompatibilityVersion", ""))
    facts["game build"] = _app_state(game).get("buildid", "")
    facts["how the game starts"] = " ".join([str(game.exe.name), *game.exe_args])
    facts["game data path"] = str(settings.get("gameDataPath", ""))
    facts["launcher-settings.json keys"] = sorted(settings)
    facts["launcher installer"] = ", ".join(
        sorted(p.name for p in game.install_dir.glob("launcher-installer-linux*"))
    )
    facts.update(_launcher_facts(game.launcher_db))
    facts["dlc_load.json keys"] = sorted(_json(game.data_dir / DLC_LOAD_FILE))
    facts["continue_game.json keys"] = sorted(_json(game.data_dir / CONTINUE_FILE))
    facts["settings.txt keys"] = sorted(_keys(_read(game.data_dir / "settings.txt")))
    empires = empire_file(game.data_dir)
    facts["empire file"] = empires.name if empires else ""
    facts["empire fields"] = sorted(_empire_fields(_read(empires) if empires else b""))
    newest = _newest_save(game)
    facts["save meta keys"] = sorted(_keys(_save_meta(newest))) if newest else []
    facts["descriptor keys"] = sorted(
        {k for p in game.mod_dir.glob("*.mod") for k in _keys(_read(p))}
    )
    facts["playset backup keys"] = _backup_keys(game.data_dir / "playsets_backup")
    facts["game folders"] = game_folders(game.install_dir)
    facts["DLC folders"] = [d.folder for d in find_dlcs(game.install_dir)]
    facts["game languages"] = sorted(
        p.name for p in (game.install_dir / "localisation").glob("*") if p.is_dir()
    )
    return facts


def workshop_times(game: Game) -> dict[str, int]:
    """Each installed Workshop mod's last update, as Steam records it. Steam
    rewrites files without a new version, so file times can't say this."""
    acf = game.library_dir / f"steamapps/workshop/appworkshop_{STELLARIS_APP_ID}.acf"
    try:
        root = _lower(_lower(parse_vdf(acf.read_text("utf-8"))).get("appworkshop"))
    except OSError, UnicodeDecodeError, VdfError:
        return {}
    items = _lower(root.get("workshopitemsinstalled", {}))
    times: dict[str, int] = {}
    for mod_id, info in items.items():
        stamp = _lower(info).get("timeupdated", "0")
        times[mod_id] = int(stamp) if isinstance(stamp, str) and stamp.isdigit() else 0
    return times


def game_folders(install_dir: Path) -> list[str]:
    """The game's folders that Conflicts reads, lowercased, as merge_rules.json
    names them: every script folder that holds a file, graphics folders two
    deep, and any deeper one with its own row. Deeper graphics folders are
    mostly models and pictures, which share their parent's rule."""
    found: set[str] = set()
    tops = [(f.rstrip("/"), 0) for f in SCRIPT_FOLDERS]
    tops += [(f.rstrip("/"), 2) for f in GRAPHICS_FOLDERS]
    for top, depth in tops:
        for root, dirs, files in os.walk(install_dir / top):
            folder = Path(root).relative_to(install_dir)
            if depth and len(folder.parts) >= depth:
                dirs.clear()
                found.add(folder.as_posix().lower())
            elif files:
                found.add(folder.as_posix().lower())
    found.update(f.rstrip("/") for f in LOCALISATION_FOLDERS if (install_dir / f).is_dir())
    found.update(f for f in load_rules().by_folder if (install_dir / f).is_dir())
    return sorted(found)


def _launcher_facts(db: Path) -> Facts:
    facts: Facts = {}
    try:
        conn = sqlite3.connect(f"{db.as_uri()}?mode=ro", uri=True)
    except sqlite3.Error:
        return facts
    try:
        tables = [
            r[0]
            for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' "
                "AND name NOT LIKE 'sqlite_%' ORDER BY name"
            )
        ]
        for table in tables:
            if table.endswith("migrations"):
                facts["launcher migration"] = _newest_migration(conn, table)
            facts[f"launcher table {table}"] = [
                _column(row) for row in conn.execute(f'PRAGMA table_info("{table}")')
            ]
    except sqlite3.Error:
        pass
    finally:
        conn.close()
    return facts


def _newest_migration(conn: sqlite3.Connection, table: str) -> str:
    try:
        row = conn.execute(f'SELECT name FROM "{table}" ORDER BY rowid DESC LIMIT 1').fetchone()
    except sqlite3.Error:
        return ""
    return str(row[0]) if row else ""


def _column(row: tuple[object, ...]) -> str:
    """`name TYPE`, then NOT NULL, DEFAULT and PRIMARY KEY where they apply."""
    _, name, kind, notnull, default, pk = row
    text = f"{name} {kind}".strip()
    if notnull:
        text += " NOT NULL"
    if default is not None:
        text += f" DEFAULT {default}"
    if pk:
        text += " PRIMARY KEY"
    return text


def _app_state(game: Game) -> dict[str, str]:
    acf = game.library_dir / f"steamapps/appmanifest_{STELLARIS_APP_ID}.acf"
    try:
        state = _lower(_lower(parse_vdf(acf.read_text("utf-8"))).get("appstate"))
    except OSError, UnicodeDecodeError, VdfError:
        return {}
    return {k: v for k, v in state.items() if isinstance(v, str)}


def _lower(value: VdfValue | None) -> dict[str, VdfValue]:
    # Valve's keys ignore case.
    return {k.lower(): v for k, v in value.items()} if isinstance(value, dict) else {}


def _json(path: Path) -> dict[str, object]:
    try:
        found = json.loads(path.read_text("utf-8-sig"))
    except OSError, ValueError:
        return {}
    return found if isinstance(found, dict) else {}


def _read(path: Path) -> bytes:
    try:
        return path.read_bytes()
    except OSError:
        return b""


def _keys(data: bytes) -> set[str]:
    """The top-level keys of a Paradox script file."""
    return {e.key.decode("utf-8", "replace").strip('"') for e in scan(data)}


def _empire_fields(data: bytes) -> set[str]:
    """Every key directly inside any empire's block."""
    found: set[str] = set()
    for entry in scan(data):
        if entry.block:
            start, end = entry.inside
            found.update(k.key.decode("utf-8", "replace") for k in scan(data, start, end))
    return found


def _newest_save(game: Game) -> Path | None:
    folders = [game.data_dir / SAVE_FOLDER, *cloud_save_dirs(game.steam_dir)]
    files = [p for f in folders for p in f.glob("*/*.sav") if p.is_file()]
    return max(files, key=lambda p: p.stat().st_mtime) if files else None


def _save_meta(path: Path) -> bytes:
    try:
        with zipfile.ZipFile(path) as zf:
            return zf.read("meta")
    except OSError, KeyError, zipfile.BadZipFile:
        return b""


def _backup_keys(folder: Path) -> list[str]:
    """The newest launcher playset backup's keys, and each mod's as `mods.<key>`."""
    files = sorted(folder.glob("*.json"), key=lambda p: p.stat().st_mtime)
    if not files:
        return []
    found = _json(files[-1])
    keys = set(found)
    mods = found.get("mods")
    for mod in mods if isinstance(mods, list) else []:
        if isinstance(mod, dict):
            keys.update(f"mods.{k}" for k in mod)
    return sorted(keys)


# --- What's wrong now ------------------------------------------------------


def problems(game: Game) -> list[Problem]:
    """What would break part of Cold Steel as things stand, baseline or not."""
    found: list[Problem] = []
    found += _launcher_problems(game.launcher_db)
    found += _start_problems(game)
    found += _paradox_file_problems(game.data_dir)
    found += _empire_problems(game.data_dir)
    found += _save_problems(game)
    found += _dlc_problems(game)
    found += _steam_problems(game)
    return found


def _launcher_problems(db: Path) -> list[Problem]:
    area = "Launcher database"
    if not db.is_file():
        return [Problem(area, f"{db} isn't there")]
    try:
        conn = sqlite3.connect(f"{db.as_uri()}?mode=ro", uri=True)
    except sqlite3.Error as error:
        return [Problem(area, f"Can't open it: {error}")]
    found: list[Problem] = []
    try:
        for table in sorted(REQUIRED_COLUMNS.keys() | WRITTEN_COLUMNS.keys()):
            # Cold Steel never adds launcher mods (decision 24), so only the
            # tables it inserts into need every required column filled.
            columns = {row[1]: row for row in conn.execute(f'PRAGMA table_info("{table}")')}
            missing = sorted(REQUIRED_COLUMNS.get(table, set()) - columns.keys())
            if missing:
                found.append(
                    Problem(
                        area,
                        f"`{table}` lacks {', '.join(missing)}. Cold Steel can't read the "
                        "launcher's playsets until launcher_db.py knows the new layout",
                    )
                )
            written = WRITTEN_COLUMNS.get(table, set())
            required = sorted(
                name
                for name, row in columns.items()
                if row[3] and row[4] is None and not row[5] and name not in written
            )
            if table in WRITTEN_COLUMNS and columns and required:
                found.append(
                    Problem(
                        area,
                        f"`{table}` now requires {', '.join(required)}, with no default. "
                        "Export and Sync launcher don't write it, so their inserts would fail",
                    )
                )
    except sqlite3.Error as error:
        found.append(Problem(area, f"Can't read it: {error}"))
    finally:
        conn.close()
    return found


def _start_problems(game: Game) -> list[Problem]:
    area = "Starting the game"
    found: list[Problem] = []
    if not game.exe.is_file():
        found.append(Problem(area, f"launcher-settings.json's exePath, {game.exe}, isn't there"))
    if game.exe.name[:15] not in GAME:
        found.append(
            Problem(
                area,
                f"The game program is now `{game.exe.name}`. processes.py looks for "
                f"{sorted(GAME)}, so Cold Steel wouldn't see the game running",
            )
        )
    if not any((game.install_dir / name).is_file() for name in LAUNCHER):
        found.append(
            Problem(
                area,
                f"No launcher program named {sorted(LAUNCHER)} in the game folder. "
                "processes.py may not see the launcher running, and Cold Steel could "
                "write its database while it's open",
            )
        )
    return found


def _paradox_file_problems(data_dir: Path) -> list[Problem]:
    found: list[Problem] = []
    extra = sorted(_json(data_dir / DLC_LOAD_FILE).keys() - DLC_LOAD_KEYS)
    if extra:
        found.append(
            Problem(
                DLC_LOAD_FILE,
                f"It has {', '.join(extra)}. Play rewrites the file without them",
            )
        )
    shown = _json(data_dir / CONTINUE_FILE)
    extra = sorted(shown.keys() - CONTINUE_KEYS)
    if extra:
        found.append(
            Problem(
                CONTINUE_FILE,
                f"It has {', '.join(extra)}. Play and Continue rewrite the file without them",
            )
        )
    title = shown.get("title")
    if isinstance(title, str) and not title.startswith(f"{SAVE_FOLDER}/"):
        found.append(
            Problem(
                CONTINUE_FILE,
                f'Its title is "{title}", not "{SAVE_FOLDER}/<folder>/<file>" as '
                "continue_game.py writes it",
            )
        )
    settings = _read(data_dir / "settings.txt")
    cloud = re.search(rb'^[ \t]*autosave_tocloud[ \t]*=[ \t]*"?(\w+)"?', settings, re.MULTILINE)
    if cloud and cloud.group(1) != b"yes":
        found.append(
            Problem(
                "settings.txt",
                f"autosave_tocloud is `{cloud.group(1).decode()}`. save.py treats anything "
                "but `yes` as off, which turns Saves and Empires on",
            )
        )
    return found


def _empire_problems(data_dir: Path) -> list[Problem]:
    area = "Empires"
    found: list[Problem] = []
    files = sorted(p.name for p in data_dir.glob(EMPIRE_GLOB))
    current = empire_file(data_dir)
    if len(files) > 1 and current:
        found.append(
            Problem(
                area,
                f"There are {len(files)} empire files ({', '.join(files)}). The game now "
                f"uses {current.name}: a new empire format. See Phase 10",
            )
        )
    if current:
        data = _read(current)
        head, blocks = split_empires(data)
        if join_empires(head, (b for _, b in blocks)) != data:
            found.append(
                Problem(
                    area,
                    f"{current.name} doesn't split into blocks and join back byte for byte",
                )
            )
        lists = load_json(lists_file(empires_dir()), ListsFile)
        game_format = file_format(current)
        other = sorted(
            owner
            for owner, info in (lists.lists.items() if lists else ())
            if info.format and info.format != game_format
        )
        if other:
            found.append(
                Problem(
                    area,
                    f"{len(other)} empire lists are in another format than the game's "
                    f"{game_format}. Play refuses them until they're converted (decision 112)",
                )
            )
    return found


def _save_problems(game: Game) -> list[Problem]:
    newest = _newest_save(game)
    if newest is None:
        return []
    missing = sorted(SAVE_META_KEYS - _keys(_save_meta(newest)))
    if not missing:
        return []
    return [
        Problem(
            "Saves",
            f"The newest save file, {newest.parent.name}/{newest.name}, has no "
            f"{', '.join(missing)} in its meta. The Saves window can't name or match it",
        )
    ]


def _dlc_problems(game: Game) -> list[Problem]:
    area = "DLC"
    dlcs = find_dlcs(game.install_dir)
    found = [
        Problem(area, f"{d.folder}'s .dlc file has no name. The DLC window shows its folder")
        for d in dlcs
        if d.name == d.folder
    ]
    try:
        conn = sqlite3.connect(f"{game.launcher_db.as_uri()}?mode=ro", uri=True)
        try:
            ids = [r[0] for r in conn.execute("SELECT DISTINCT dlcId FROM playsets_dlcs")]
        finally:
            conn.close()
    except sqlite3.Error:
        ids = []
    unknown = sorted(i for i in ids if not launcher_dlc_folder(i, dlcs))
    if unknown:
        found.append(
            Problem(
                area,
                f"The launcher's DLC ids {', '.join(unknown)} match no DLC folder. "
                "dlc.py's launcher_dlc_folder needs a rule for them",
            )
        )
    return found


def _steam_problems(game: Game) -> list[Problem]:
    area = "Steam"
    found: list[Problem] = []
    for path in (
        game.steam_dir / "steamapps/libraryfolders.vdf",
        game.steam_dir / "config/loginusers.vdf",
        game.library_dir / f"steamapps/workshop/appworkshop_{STELLARIS_APP_ID}.acf",
    ):
        if not path.is_file():
            continue  # Cold Steel copes without each: no other libraries, users or mods
        try:
            parse_vdf(path.read_text("utf-8"))
        except (OSError, UnicodeDecodeError, VdfError) as error:
            found.append(Problem(area, f"Can't read {path.name}: {error}"))
    return found


# --- Comparing with the baseline ---------------------------------------------


@dataclass(frozen=True)
class Change:
    fact: str
    old: Value | None  # None: the fact is new
    new: Value | None  # None: the fact is gone

    @property
    def reader(self) -> str:
        return next((v for k, v in READERS.items() if self.fact.startswith(k)), "")

    def lines(self) -> list[str]:
        if isinstance(self.old, list) and isinstance(self.new, list):
            added = [x for x in self.new if x not in self.old]
            removed = [x for x in self.old if x not in self.new]
            if self.fact == "game folders":
                return [f"+ {x}  ({folder_rule(x)})" for x in added] + [
                    f"- {x}  ({folder_rule(x)})" for x in removed
                ]
            return [f"+ {x}" for x in added] + [f"- {x}" for x in removed]
        return [f"was: {_shown(self.old)}", f"now: {_shown(self.new)}"]


def folder_rule(folder: str) -> str:
    """Which merge_rules.json row a game folder gets, in a few words."""
    rules = load_rules()
    rule = rules.for_folder(folder)
    row = (
        "its own row"
        if folder in rules.by_folder
        else ("the default row" if rule is rules.default else f"the row for {rule.folder}")
    )
    return f"{row}: {rule.winner} wins, by {rule.unit}"


def compare(old: Facts, new: Facts) -> list[Change]:
    keys = [*new, *(k for k in old if k not in new)]
    return [Change(k, old.get(k), new.get(k)) for k in keys if old.get(k) != new.get(k)]


def _shown(value: Value | None) -> str:
    if value is None:
        return "(not there)"
    if isinstance(value, list):
        return ", ".join(value) or "(empty)"
    return value or "(empty)"


# --- Patch notes -----------------------------------------------------------

NEWS = (
    "https://api.steampowered.com/ISteamNews/GetNewsForApp/v2/"
    f"?appid={STELLARIS_APP_ID}&count=10&maxlength=0&feeds=steam_community_announcements"
)
# A note line about something Cold Steel reads or writes, by topic.
TOPICS = {
    "Empires": r"empire (creator|design)|custom empire|premade|user_empire",
    "Launcher and playsets": r"launcher|playset",
    "Saves and Continue": r"\bsaves?\b|save ?game|autosave|continue_?(game|last|button)|"
    r"steam cloud|\bcloud\b",
    "DLC": r"\bdlcs?\b",
    "Localisation": r"locali[sz]ation|\breplace/|language",
    "Error log": r"error\.log|error log|logs? an error",
    "Linux and starting the game": r"linux|command.line|-gdpr|nakama|crash on start",
    # Lines starting "Mods" only: "Wet Climate Mods" is a planet preference.
    # A file moved or renamed changes which mod copies clash with the game's.
    "Mods, load order and overrides": r"^-? ?mods?\b|\bmodded\b|descriptor|supported_version|"
    r"load order|override|duplicate|already exists|workshop|\.txt\b|\bfolder",
}


@dataclass(frozen=True)
class Note:
    date: datetime
    title: str
    url: str
    text: str

    @property
    def file_name(self) -> str:
        slug = re.sub(r"[^a-z0-9]+", "-", self.title.casefold()).strip("-")[:60]
        return f"{self.date:%Y-%m-%d}-{slug}.txt"


def fetch_notes() -> list[Note]:
    """Stellaris's newest announcements, in full. Steam's news API gives the
    whole text: a web page summary can stop before the modding notes."""
    with urllib.request.urlopen(NEWS, timeout=30) as response:
        items = json.load(response)["appnews"]["newsitems"]
    return [
        Note(
            datetime.fromtimestamp(int(i["date"]), UTC),
            str(i["title"]),
            str(i.get("url", "")),
            plain_text(str(i["contents"])),
        )
        for i in items
    ]


def keep_notes(notes: Iterable[Note], folder: Path) -> None:
    """Steam's API lists only the newest few, so each one is kept as it's seen."""
    folder.mkdir(parents=True, exist_ok=True)
    for note in notes:
        path = folder / note.file_name
        if not path.exists():
            path.write_text(
                f"{note.title}\n{note.url}\n{note.date.isoformat()}\n\n{note.text}", "utf-8"
            )


def kept_notes(folder: Path) -> list[Note]:
    """Every kept note, oldest first."""
    found: list[Note] = []
    for path in sorted(folder.glob("*.txt")):
        title, url, stamp, _, text = (path.read_text("utf-8").split("\n", 4) + [""] * 5)[:5]
        try:
            found.append(Note(datetime.fromisoformat(stamp), title, url, text))
        except ValueError:
            continue
    return sorted(found, key=lambda n: n.date)


def plain_text(bbcode: str) -> str:
    """Steam's BBCode as plain lines: headings as `## `, list items as `- `."""
    text = re.sub(r"\[h\d\](.*?)\[/h\d\]", r"\n## \1\n", bbcode, flags=re.DOTALL)
    text = re.sub(r"\[\*\]", "\n- ", text)
    text = re.sub(r"\[/?p\]", "\n", text)
    text = re.sub(r"\[/?[a-z0-9*]+(?:[= ][^\]]*)?\]", "", text)
    text = "\n".join(line.strip() for line in text.splitlines())
    text = re.sub(r"^- *\n+", "- ", text, flags=re.MULTILINE)  # an item's text on its line
    return re.sub(r"\n{3,}", "\n\n", text).strip() + "\n"


def note_topics(text: str) -> dict[str, list[str]]:
    """Each line of a note that touches a topic, under that topic, with the
    heading it's under. A line goes under its first topic only, so the narrower
    topics come first.

    Steam's notes mark few headings. A short line that isn't a list item and
    doesn't end a sentence, like "Modding", counts as one."""
    found: dict[str, list[str]] = {}
    heading = ""
    for line in text.splitlines():
        if line.startswith("## "):
            heading = line[3:].strip()
            continue
        if not line.startswith("- ") and 0 < len(line.split()) <= 4 and line[-1] not in ".!?:":
            heading = line
            continue
        for topic, pattern in TOPICS.items():
            if re.search(pattern, line, re.IGNORECASE):
                where = f" ({heading})" if heading else ""
                found.setdefault(topic, []).append(f"{line.removeprefix('- ')}{where}")
                break
    return found


# --- The report --------------------------------------------------------------


class BuildStamp(msgspec.Struct):
    """The part of a build's record this check needs. The rest, a list of
    every file, can be megabytes."""

    name: str = ""
    built: str = ""  # local time, "2026-10-02 14:03"
    order: tuple[str, ...] = ()  # the mods inside it, by Mod.key


def stale_builds(game: Game, workshop: dict[str, int], root: Path) -> list[str]:
    """Each build made before the game's last update, or before a Workshop mod
    inside it updated. A build keeps its mods as they were: a rebuild takes
    the update. Pinned copies (`workshop:<id>@<copy>`) never change."""
    game_updated = _game_updated(game)
    out: list[str] = []
    for path in sorted(root.glob("*.json")):
        stamp = load_json(path, BuildStamp)
        if stamp is None or not stamp.built:
            continue
        try:
            built = datetime.strptime(stamp.built, "%Y-%m-%d %H:%M").timestamp()
        except ValueError:
            continue
        why = ["the game"] if game_updated > built else []
        updated = [
            key.removeprefix("workshop:")
            for key in stamp.order
            if key.startswith("workshop:")
            and workshop.get(key.removeprefix("workshop:"), 0) > built
        ]
        why += [f"{_mod_name(game, i)} ({i})" for i in updated]
        if why:
            out.append(f"- **{stamp.name}**, built {stamp.built}, is older than: {', '.join(why)}")
    return out


def error_log_lines(game: Game, since: float) -> list[str]:
    """How well the error log's entries still read. Its shape is only known
    after a game run on the new version."""
    log = game.data_dir / "logs/error.log"
    try:
        stamp = log.stat().st_mtime
        entries = read_error_log(game.data_dir)
    except OSError:
        return ["No error.log yet."]
    when = datetime.fromtimestamp(stamp).strftime("%Y-%m-%d %H:%M")
    if stamp < since:
        return [
            f"error.log is from {when}, before the game's last update. Play once on "
            "the new version, then run the check again."
        ]
    named = sum(1 for e in entries if e.file)
    share = f"{named * 100 // len(entries)}%" if entries else "none"
    return [
        f"error.log from {when}: {len(entries)} entries, {share} of them name a file. "
        "Open Errors in Cold Steel and check they still sort into mods."
    ]


def render(
    game: Game,
    base: Baseline | None,
    facts: Facts,
    workshop: dict[str, int],
    found: list[Problem],
    notes: list[Note] | None,
) -> str:
    out = [f"# Update check, {datetime.now():%Y-%m-%d %H:%M}", ""]
    if base:
        out.append(f"Against the baseline accepted {base.accepted[:16].replace('T', ' ')}.")
    else:
        out.append("No baseline yet: nothing to compare with. Accept this one once it's read.")
    out += ["", "## What updated", ""]
    out += _updated(game, base, facts, workshop)

    out += ["", "## Problems now", ""]
    out += [f"- **{p.area}.** {p.text}." for p in found] or ["None."]

    out += ["", "## Changed since the baseline", ""]
    changes = compare(base.facts, facts) if base else []
    for change in changes:
        out.append(f"### {change.fact}")
        if change.reader:
            out.append(f"In Cold Steel: {change.reader}.")
        out += ["", "```", *change.lines(), "```", ""]
    if not changes:
        out.append("Nothing." if base else "Nothing to compare with.")

    out += ["", "## Builds older than an update", ""]
    out += stale_builds(game, workshop, builds_dir()) or ["None."]

    out += ["", "## Error log", ""]
    out += error_log_lines(game, _game_updated(game))

    if notes is not None:
        out += ["", "## Patch notes", ""]
        out += _notes(notes, base)
    return "\n".join(out).rstrip() + "\n"


def _updated(
    game: Game, base: Baseline | None, facts: Facts, workshop: dict[str, int]
) -> list[str]:
    lines = [f"- **Game**: {game.version_name}, build {_shown(facts.get('game build'))}"]
    if base:
        for fact in ("game version", "game build", "launcher installer", "launcher migration"):
            if base.facts.get(fact) != facts.get(fact):
                lines.append(
                    f"- **{fact}** changed: {_shown(base.facts.get(fact))} → "
                    f"{_shown(facts.get(fact))}"
                )
        changed = sorted(i for i in workshop if workshop[i] > base.workshop.get(i, 0))
        new = [i for i in changed if i not in base.workshop]
        gone = sorted(set(base.workshop) - set(workshop))
        if changed:
            lines.append(f"- **Workshop mods updated or added**: {len(changed)}")
            lines += [
                f"  - {_mod_name(game, i)} ({i}){', new' if i in new else ''}" for i in changed
            ]
        if gone:
            lines.append(f"- **Workshop mods gone**: {', '.join(gone)}")
    lines.append(f"- **Cold Steel**: {__version__}")
    return lines


def _mod_name(game: Game, mod_id: str) -> str:
    try:
        return decode_descriptor((game.mod_dir / f"ugc_{mod_id}.mod").read_bytes()).name or mod_id
    except OSError:
        return mod_id


def _game_updated(game: Game) -> float:
    stamp = _app_state(game).get("lastupdated", "0")
    return float(stamp) if stamp.isdigit() else 0.0


def _notes(notes: list[Note], base: Baseline | None) -> list[str]:
    since = datetime.fromisoformat(base.accepted) if base else None
    recent = [n for n in notes if n.date > since] if since else notes[-3:]
    if not recent:
        return ["No announcements since the baseline." if base else "No announcements found."]
    out: list[str] = []
    for note in recent:
        out += [f"### {note.title}", f"{note.date:%Y-%m-%d}, {note.url}", ""]
        out.append(f"Kept in full: {paths.shown(notes_dir() / note.file_name)}")
        topics = note_topics(note.text)
        for topic, lines in topics.items():
            out += ["", f"**{topic}**", *(f"- {line}" for line in lines)]
        if not topics:
            out.append("No line touches what Cold Steel reads or writes.")
        out.append("")
    return out


def gather_notes() -> list[Note]:
    """Steam's newest notes, kept for next time, with every note kept before.
    Oldest first. Either half failing still gives the other."""
    fetched: list[Note] = []
    try:
        fetched = fetch_notes()
    except (OSError, ValueError, KeyError, TypeError) as error:
        print(f"Couldn't fetch Steam's news, so only kept notes are shown: {error}\n")
    try:
        keep_notes(fetched, notes_dir())
    except OSError as error:
        print(f"Couldn't keep the notes: {error}\n")
    by_name = {n.file_name: n for n in [*kept_notes(notes_dir()), *fetched]}
    return sorted(by_name.values(), key=lambda n: n.date)


def find_this_game() -> Game:
    """The game Cold Steel finds, with the Steam and data folders from its settings."""
    settings = load_settings()
    steam = (Path(settings.steam_dir),) if settings.steam_dir else DEFAULT_STEAM_DIRS
    game = find_game(steam)
    if settings.game_data_dir:
        game = replace(game, data_dir=Path(settings.game_data_dir))
    return game


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0] if __doc__ else "")
    parser.add_argument("--notes", action="store_true", help="fetch Steam's patch notes too")
    parser.add_argument("--accept", action="store_true", help="save now as the next baseline")
    args = parser.parse_args()
    try:
        game = find_this_game()
    except GameNotFound as error:
        print(error, file=sys.stderr)
        return 1

    facts = collect(game)
    workshop = workshop_times(game)
    if args.accept:
        stamp = datetime.now(UTC).isoformat(timespec="seconds")
        save_json(baseline_file(), Baseline(stamp, __version__, facts, workshop))
        print(f"Accepted. The next check compares with {paths.shown(baseline_file())}.")
        return 0

    notes = gather_notes() if args.notes else None
    base = load_json(baseline_file(), Baseline)
    print(render(game, base, facts, workshop, problems(game), notes), end="")
    return 0


if __name__ == "__main__":
    sys.exit(main())
