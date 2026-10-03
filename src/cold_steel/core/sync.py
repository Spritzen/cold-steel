"""Moving playsets between Cold Steel and the Paradox launcher.

Import copies a launcher playset into ours. Export writes one of ours into the
launcher database, after a backup, and only while the launcher is closed.
Open in launcher exports, makes the playset the launcher's active one, then
asks Steam to start the game, which opens the launcher. Sync launcher exports
every playset at once and removes the launcher's others.
"""

import subprocess
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
