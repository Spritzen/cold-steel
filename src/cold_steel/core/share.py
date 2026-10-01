"""Playset files to share with other players.

We write the shape the Paradox launcher uses for its own playset backups, which
Irony can also export ("Paradox launcher JSON"):

    {"game": "stellaris", "name": "My playset",
     "mods": [{"displayName": "UI Overhaul Dynamic", "enabled": true,
               "position": 0, "steamId": "1623423360"}]}

We read that, and Irony's own collection export: a .zip holding `exported.json`
with the collection's `Name` and its `Mods` as "mod/ugc_<id>.mod" paths.

Local mods aren't shared: another player can't have them.
"""

import json
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

from cold_steel.core.library import Library
from cold_steel.core.playsets import new_id
from cold_steel.store.files import save_json
from cold_steel.store.playsets import Playset, PlaysetEntry

IRONY_ENTRY = "exported.json"


class ShareError(Exception):
    pass


@dataclass(frozen=True)
class Shared:
    playset: Playset
    left_out: tuple[str, ...] = ()  # names of mods that couldn't be included


def save_share_file(playset: Playset, library: Library, path: Path) -> tuple[str, ...]:
    """Write the file. Returns the names of local mods left out."""
    names = {m.key: m.name for m in library.mods}
    mods: list[dict[str, Any]] = []
    left_out: list[str] = []
    for entry in playset.entries:
        source, _, ident = entry.key.partition(":")
        name = names.get(entry.key) or entry.name or ident
        if source != "workshop":
            left_out.append(name)
            continue
        mods.append(
            {"displayName": name, "enabled": entry.enabled, "position": len(mods), "steamId": ident}
        )
    save_json(path, {"game": "stellaris", "name": playset.name, "mods": mods})
    return tuple(left_out)


def load_share_file(path: Path) -> Shared:
    """Read a shared playset. Its mods may not all be installed: those show as missing."""
    try:
        if zipfile.is_zipfile(path):
            with zipfile.ZipFile(path) as zf:
                data = json.loads(zf.read(IRONY_ENTRY).decode("utf-8-sig"))
        else:
            data = json.loads(path.read_text("utf-8-sig"))
    except KeyError as error:
        raise ShareError(f"{path.name} is a zip, but not one Irony exported") from error
    except (OSError, ValueError, zipfile.BadZipFile) as error:
        raise ShareError(f"Couldn't read {path.name}: {error}") from error
    if not isinstance(data, dict):
        raise ShareError(f"{path.name} isn't a playset file")

    fields = {k.casefold(): v for k, v in data.items()}  # Irony writes "Name", Paradox "name"
    name = str(fields.get("name") or path.stem)
    mods = fields.get("mods")
    if not isinstance(mods, list):
        raise ShareError(f"{path.name} has no list of mods")
    left_out: list[str] = []
    if all(isinstance(m, dict) for m in mods):
        entries = _launcher_entries(mods, left_out)
    else:
        entries = _irony_entries(mods, fields.get("modnames"))
    return Shared(Playset(id=new_id(), name=name, entries=tuple(entries)), tuple(left_out))


def _launcher_entries(mods: list[Any], left_out: list[str]) -> list[PlaysetEntry]:
    rows = [{k.casefold(): v for k, v in m.items()} for m in mods]

    def position(m: dict[str, Any]) -> int:
        value = m.get("position")
        return value if isinstance(value, int) else 1 << 30

    rows.sort(key=position)
    entries: list[PlaysetEntry] = []
    for m in rows:
        steam_id = str(m.get("steamid") or "")
        name = str(m.get("displayname") or m.get("name") or "")
        if steam_id.isdigit():
            entries.append(PlaysetEntry(f"workshop:{steam_id}", bool(m.get("enabled", True)), name))
        else:  # a Paradox Mods entry, or a local one: no Workshop ID to find it by
            left_out.append(name or "a mod with no name")
    return entries


def _irony_entries(mods: list[Any], names: Any) -> list[PlaysetEntry]:
    names = names if isinstance(names, list) else []
    entries: list[PlaysetEntry] = []
    for i, descriptor in enumerate(mods):
        stem = PurePosixPath(str(descriptor).replace("\\", "/")).stem
        ident = stem.removeprefix("ugc_")
        key = f"workshop:{ident}" if ident.isdigit() else f"local:{stem}"
        name = str(names[i]) if i < len(names) else ""
        entries.append(PlaysetEntry(key, True, name))
    return entries
