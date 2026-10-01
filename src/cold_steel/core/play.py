"""Play: tell the game which mods and DLC to load, then start it.

The game reads `dlc_load.json` as it starts, so writing that file is what
makes a playset take effect. The game is started directly, not through Steam,
so the Paradox launcher doesn't open. Steam must be running.
"""

import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from cold_steel.core.library import Library
from cold_steel.core.mods import Mod
from cold_steel.paradox import processes
from cold_steel.paradox.descriptor import Descriptor, format_descriptor
from cold_steel.paradox.dlc_load import DlcLoad, write_dlc_load
from cold_steel.paradox.game import Game
from cold_steel.store.playsets import Playset


class PlayError(Exception):
    pass


@dataclass(frozen=True)
class PlayPlan:
    load: DlcLoad
    # mod/*.mod files the game needs but that don't exist yet: path -> text
    new_descriptors: dict[Path, str] = field(default_factory=dict)
    skipped: tuple[str, ...] = ()  # names of turned-on mods that aren't installed


def plan_play(playset: Playset, library: Library) -> PlayPlan:
    installed = {m.key: m for m in library.mods}
    game = library.game
    enabled: list[str] = []
    new: dict[Path, str] = {}
    skipped: list[str] = []
    for entry in playset.entries:
        if not entry.enabled:
            continue
        mod = installed.get(entry.key)
        if mod is None:
            skipped.append(entry.name or entry.key)
            continue
        outer = Path(mod.descriptor_file) if mod.descriptor_file else None
        if outer is None or outer.parent != game.mod_dir:
            # Steam downloaded it, but the launcher never ran to write its .mod file.
            outer = game.mod_dir / f"ugc_{mod.remote_file_id or mod.key.partition(':')[2]}.mod"
            if not outer.exists():
                new[outer] = format_descriptor(_descriptor(mod))
        enabled.append(f"mod/{outer.name}")

    off = set(playset.disabled_dlcs)
    disabled = tuple(d.file for d in library.dlcs if d.folder in off)
    return PlayPlan(DlcLoad(tuple(enabled), disabled), new, tuple(skipped))


def play(plan: PlayPlan, game: Game, backup_dir: Path) -> subprocess.Popen[bytes]:
    """Check, write the files the plan needs, then start the game.

    Every check runs before anything is written, so a refusal changes nothing.
    """
    if processes.running(processes.GAME):
        raise PlayError("Stellaris is already running. Close it first.")
    if not processes.running(processes.STEAM):
        raise PlayError("Steam isn't running. Start Steam, then press Play again.")
    if not game.exe.is_file():
        raise PlayError(f"The game program isn't there: {game.exe}")
    write_plan(plan, game, backup_dir)
    try:
        # Its own session, so closing Cold Steel doesn't close the game.
        return subprocess.Popen(
            [str(game.exe), *game.exe_args],
            cwd=game.install_dir,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
    except OSError as error:
        raise PlayError(f"Couldn't start {game.exe}: {error}") from error


def write_plan(plan: PlayPlan, game: Game, backup_dir: Path) -> None:
    """Add any missing mod/*.mod files, then write dlc_load.json after a backup."""
    for path, text in plan.new_descriptors.items():
        if not path.exists():  # only ever add a missing one, never overwrite
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, "utf-8")
    try:
        write_dlc_load(game.data_dir, plan.load, backup_dir)
    except OSError as error:
        raise PlayError(f"Couldn't write dlc_load.json: {error}") from error


def _descriptor(mod: Mod) -> Descriptor:
    return Descriptor(
        name=mod.name,
        version=mod.version,
        supported_version=mod.supported_version,
        tags=mod.tags,
        dependencies=mod.dependencies,
        path=mod.root if not mod.archive else "",
        archive=mod.archive,
        remote_file_id=mod.remote_file_id or mod.key.partition(":")[2],
    )
