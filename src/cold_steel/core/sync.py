"""Moving playsets between Cold Steel and the Paradox launcher.

Import copies a launcher playset into ours. Export writes one of ours into the
launcher database, after a backup, and only while the launcher is closed.
"""

from pathlib import Path

import msgspec

from cold_steel.core.library import Library
from cold_steel.core.playsets import PlaysetBook
from cold_steel.paradox import processes
from cold_steel.paradox.dlc import launcher_dlc_folder
from cold_steel.paradox.launcher_db import ExportMod, WriteResult, write_playset
from cold_steel.store.playsets import Playset


class SyncError(Exception):
    pass


def import_playset(book: PlaysetBook, launcher_playset: Playset) -> Playset:
    """Add a copy of a launcher playset (from Library.launcher_playsets) to ours."""
    name = book.unused_name(launcher_playset.name)
    return book.add(msgspec.structs.replace(launcher_playset, name=name))


def export_playset(
    book: PlaysetBook, playset: Playset, library: Library, backup_dir: Path
) -> WriteResult:
    """Write the playset into the launcher database, and remember which
    launcher playset it became so the next export replaces it."""
    if processes.running(processes.LAUNCHER):
        raise SyncError(
            "The Paradox launcher is open. Close it first: it would overwrite the change."
        )
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

    result = write_playset(
        library.game.launcher_db,
        name=playset.name,
        mods=mods,
        backup_dir=backup_dir,
        launcher_id=playset.launcher_id,
        dlc_enabled=dlc_enabled,
    )
    if result.launcher_id != playset.launcher_id:
        book.update(msgspec.structs.replace(playset, launcher_id=result.launcher_id))
    return result
