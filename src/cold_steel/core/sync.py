"""Moving playsets between Cold Steel and the Paradox launcher.

Import copies a launcher playset into ours. Export writes one of ours into the
launcher database, after a backup, and only while the launcher is closed.
Open in launcher exports, makes the playset the launcher's active one, then
asks Steam to start the game, which opens the launcher. Sync launcher exports
every playset at once and removes the launcher's others.

A playset we imported or exported remembers its launcher copy, so we can say
when the two have drifted apart (launcher_difference).
"""

import subprocess
from dataclasses import dataclass
from pathlib import Path

import msgspec

from cold_steel.core.library import Library
from cold_steel.core.playsets import PlaysetBook
from cold_steel.paradox import processes
from cold_steel.paradox.dlc import launcher_dlc_folder
from cold_steel.paradox.game import STELLARIS_APP_ID, Game
from cold_steel.paradox.launcher_db import (
    ExportMod,
    PlaysetWrite,
    ReplaceResult,
    WriteResult,
    replace_playsets,
    write_playset,
)
from cold_steel.store.playsets import Playset

# How many mod names a difference names before "and N more".
NAMED = 3


class SyncError(Exception):
    pass


def import_playset(book: PlaysetBook, launcher_playset: Playset) -> Playset:
    """Add a copy of a launcher playset (from Library.launcher_playsets) to ours."""
    name = book.unused_name(launcher_playset.name)
    return book.add(msgspec.structs.replace(launcher_playset, name=name))


def export_playset(
    book: PlaysetBook,
    playset: Playset,
    library: Library,
    backup_dir: Path,
    *,
    active: bool = False,
) -> WriteResult:
    """Write the playset into the launcher database, and remember which
    launcher playset it became so the next export replaces it. `active` makes
    it the one the launcher shows when it opens."""
    _refuse_if_launcher_open()
    write = _launcher_playset(playset, library)
    result = write_playset(
        library.game.launcher_db,
        name=write.name,
        mods=write.mods,
        backup_dir=backup_dir,
        launcher_id=write.launcher_id,
        dlc_enabled=write.dlc_enabled,
        active=active,
    )
    if result.launcher_id != playset.launcher_id:
        book.update(msgspec.structs.replace(playset, launcher_id=result.launcher_id))
    return result


def sync_launcher(book: PlaysetBook, library: Library, backup_dir: Path) -> ReplaceResult:
    """Replace every playset in the launcher with ours, in our order, after one
    backup. Our active playset becomes the launcher's."""
    _refuse_if_launcher_open()
    playsets = book.playsets
    if not playsets:
        raise SyncError("Cold Steel has no playsets, so the launcher was left alone.")
    ids = [p.id for p in playsets]
    result = replace_playsets(
        library.game.launcher_db,
        [_launcher_playset(p, library) for p in playsets],
        backup_dir=backup_dir,
        active=ids.index(book.active) if book.active in ids else None,
    )
    for playset, launcher_id in zip(playsets, result.launcher_ids, strict=True):
        if launcher_id != playset.launcher_id:
            book.update(msgspec.structs.replace(playset, launcher_id=launcher_id))
    return result


@dataclass(frozen=True)
class LauncherDifference:
    """How the launcher's copy of a playset differs from ours."""

    missing: tuple[str, ...] = ()  # names of mods ours has and the launcher's copy doesn't
    extra: tuple[str, ...] = ()  # names of mods only the launcher's copy has
    switched: tuple[str, ...] = ()  # names of mods on in one copy and off in the other
    order: bool = False  # the mods both have are in a different order
    name: str = ""  # the launcher's name for it, if that differs

    def __bool__(self) -> bool:
        return bool(self.missing or self.extra or self.switched or self.order or self.name)


def launcher_difference(playset: Playset, library: Library) -> LauncherDifference:
    """How the launcher's copy of this playset (by its launcher_id) differs from it.

    Empty when they match, or there's no copy to compare with. Mods that aren't
    installed are left out on both sides: export can't write them either, so
    they'd never match. DLCs aren't compared, as the launcher only lists the
    DLCs it knows.
    """
    theirs = next((p for p in library.launcher_playsets if p.id == playset.launcher_id), None)
    if not playset.launcher_id or theirs is None:
        return LauncherDifference()
    installed = {m.key: m.name for m in library.mods}
    ours_on = {e.key: e.enabled for e in playset.entries if e.key in installed}
    theirs_on = {e.key: e.enabled for e in theirs.entries if e.key in installed}
    both = [k for k in ours_on if k in theirs_on]
    return LauncherDifference(
        missing=tuple(installed[k] for k in ours_on if k not in theirs_on),
        extra=tuple(installed[k] for k in theirs_on if k not in ours_on),
        switched=tuple(installed[k] for k in both if ours_on[k] != theirs_on[k]),
        order=both != [k for k in theirs_on if k in ours_on],
        name=theirs.name if theirs.name != playset.name else "",
    )


def describe_difference(diff: LauncherDifference) -> str:
    """One line for the user, naming a few of the mods."""
    parts: list[str] = []
    for names, what in (
        (diff.missing, "doesn't have"),
        (diff.extra, "also has"),
        (diff.switched, "has a different on/off setting for"),
    ):
        if names:
            shown = ", ".join(names[:NAMED])
            if len(names) > NAMED:
                shown += f" and {len(names) - NAMED} more"
            parts.append(f"{what} {shown}")
    if diff.order:
        parts.append("has the mods in a different order")
    if diff.name:
        parts.append(f"is called \u201c{diff.name}\u201d")
    return (
        "The launcher's copy of this playset is out of date: it "
        + "; it ".join(parts)
        + ". Starting it from the launcher plays that copy. Export to launcher, or "
        "File \u203a Sync launcher, updates it."
    )


def _refuse_if_launcher_open() -> None:
    if processes.running(processes.LAUNCHER):
        raise SyncError(
            "The Paradox launcher is open. Close it first: it would overwrite the change."
        )


def _launcher_playset(playset: Playset, library: Library) -> PlaysetWrite:
    """Our playset in the shape the launcher database is written in."""
    installed = {m.key: m for m in library.mods}
    mods: list[ExportMod] = []
    for entry in playset.entries:
        mod = installed.get(entry.key)
        source, _, ident = entry.key.partition(":")
        mods.append(
            ExportMod(
                name=mod.name if mod else entry.name or entry.key,
                steam_id=ident if source == "workshop" else "",
                dir_path=mod.root if mod else "",
                archive_path=mod.archive if mod else "",
                enabled=entry.enabled,
            )
        )

    off = set(playset.disabled_dlcs)

    def dlc_enabled(dlc_id: str) -> bool | None:
        folder = launcher_dlc_folder(dlc_id, library.dlcs)
        return (folder not in off) if folder else None

    return PlaysetWrite(playset.name, mods, playset.launcher_id, dlc_enabled)


def check_launcher_can_open() -> None:
    """Raise SyncError if the launcher can't be started now. Changes nothing."""
    if processes.running(processes.LAUNCHER):
        raise SyncError("The Paradox launcher is already open. Close it first, then try again.")
    if processes.running(processes.GAME):
        raise SyncError("Stellaris is running. Close it first.")
    if not processes.running(processes.STEAM):
        raise SyncError("Steam isn't running. Start Steam, then try again.")


def start_launcher(game: Game) -> subprocess.Popen[bytes]:
    """Ask Steam to start Stellaris, which opens the launcher. Started directly,
    the launcher can't sign in: it needs Steam to start it."""
    url = f"steam://rungameid/{STELLARIS_APP_ID}"
    try:
        # Its own session, so closing Cold Steel doesn't close anything Steam starts.
        return subprocess.Popen(
            ["xdg-open", url],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
    except OSError as error:
        raise SyncError(f"Couldn't open {url}: {error}") from error
