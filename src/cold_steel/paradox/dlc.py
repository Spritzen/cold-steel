"""The DLC installed with the game: one folder per DLC under `dlc/`, each with a
`.dlc` file naming it. See docs/reference/stellaris-files.md.

`dlc_load.json` names a DLC by its file, "dlc/dlc033_cosmic_storms/dlc033.dlc".
The launcher names it by an id that is either the folder name or the folder
name without its number ("arachnoid" for dlc002_arachnoid).
"""

import re
from dataclasses import dataclass
from pathlib import Path

from cold_steel.paradox.script import ParseError, parse

_NUMBER = re.compile(r"^(dlc\d+)_")


@dataclass(frozen=True)
class Dlc:
    folder: str  # "dlc033_cosmic_storms"
    file: str  # "dlc/dlc033_cosmic_storms/dlc033.dlc", as dlc_load.json writes it
    name: str  # "Cosmic Storms"


def find_dlcs(install_dir: Path) -> tuple[Dlc, ...]:
    """Every DLC in the game folder, in folder order (which is release order)."""
    found: list[Dlc] = []
    try:
        folders = sorted(p for p in (install_dir / "dlc").iterdir() if p.is_dir())
    except OSError:
        return ()
    for folder in folders:
        files = sorted(folder.glob("*.dlc"))
        if not files:
            continue
        found.append(
            Dlc(
                folder=folder.name,
                file=f"dlc/{folder.name}/{files[0].name}",
                name=_dlc_name(files[0]) or folder.name,
            )
        )
    return tuple(found)


def launcher_dlc_folder(dlc_id: str, dlcs: tuple[Dlc, ...]) -> str:
    """The folder for one of the launcher's DLC ids, or "" if none matches.

    Tried in turn: the whole folder name, the folder name without its number,
    then the number alone (the launcher calls dlc032_machine_age "dlc032_cybernetics").
    """
    for dlc in dlcs:
        if dlc_id == dlc.folder or dlc_id == _NUMBER.sub("", dlc.folder):
            return dlc.folder
    number = _NUMBER.match(dlc_id)
    if number:
        for dlc in dlcs:
            if dlc.folder.startswith(number.group(0)):
                return dlc.folder
    return ""


def _dlc_name(path: Path) -> str:
    try:
        nodes = parse(path.read_text("utf-8-sig", errors="replace"))
    except OSError, ParseError:
        return ""
    for node in nodes:
        if node.key == "name" and isinstance(node.value, str):
            return node.value
    return ""
