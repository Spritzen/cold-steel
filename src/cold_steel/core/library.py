"""The scan job: find the game, every mod and the launcher's playsets.

    library = Scanner(steam_dirs, cache_file)(ctx)

Unchanged mods come from the cache (decision 9). Nothing outside our cache
file is written.

Mods are read one after another. Checking timestamps is Python work that holds
the GIL, so threads made it slower (0.28 s alone, 0.42 s with 8 threads, on
55 mods / 72,000 files). Parsing for conflicts (index.py) is where parallel
work pays.
"""

import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

import msgspec

from cold_steel.core.jobs import JobContext
from cold_steel.core.mods import (
    PIN_PREFIX,
    CachedDescriptor,
    CachedMod,
    Mod,
    ModSource,
    Stamp,
    is_pinned,
    pinned_key,
    read_mod,
    read_outer,
)
from cold_steel.core.version import is_outdated
from cold_steel.paradox.dlc import Dlc, find_dlcs, launcher_dlc_folder
from cold_steel.paradox.game import DEFAULT_STEAM_DIRS, Game, find_game
from cold_steel.paradox.launcher_db import (
    LauncherData,
    LauncherDbError,
    LauncherPlayset,
    read_launcher,
)
from cold_steel.store import paths
from cold_steel.store.files import load_msgpack, save_msgpack
from cold_steel.store.playsets import Playset, PlaysetEntry
from cold_steel.store.settings import Settings

# Bump when Mod or CachedMod change shape, so old caches are thrown away.
CACHE_VERSION = 3


class CacheData(msgspec.Struct):
    version: int = CACHE_VERSION
    descriptors: dict[str, CachedDescriptor] = msgspec.field(default_factory=dict)
    mods: dict[str, CachedMod] = msgspec.field(default_factory=dict)


@dataclass(frozen=True)
class Library:
    game: Game
    mods: tuple[Mod, ...]  # installed mods, by name
    # Pinned copies of Workshop mods (snapshots.py). They're played in place of
    # the mod, never listed as mods of their own.
    pinned: tuple[Mod, ...] = ()
    dlcs: tuple[Dlc, ...] = ()
    # The launcher's playsets, for importing. Ours are in core.playsets.
    launcher_playsets: tuple[Playset, ...] = ()
    launcher_active: str = ""  # the id of the launcher's active playset
    problems: tuple[str, ...] = ()  # things that stopped part of the scan
    # Every file in each installed mod, by Mod.key: path inside the mod -> Stamp.
    # A zipped mod lists only its zip.
    files: Mapping[str, Mapping[str, Stamp]] = field(
        default_factory=dict, compare=False, repr=False
    )
    _outdated: dict[str, bool] = field(default_factory=dict, compare=False, repr=False)

    @property
    def every_mod(self) -> tuple[Mod, ...]:
        """Everything the game can load: the mods, then the pinned copies."""
        return self.mods + self.pinned

    def is_outdated(self, mod: Mod) -> bool:
        if mod.key not in self._outdated:
            self._outdated[mod.key] = is_outdated(mod.supported_version, self.game.version)
        return self._outdated[mod.key]


@dataclass
class Scanner:
    """A scan job with its settings. Call it with a JobContext to run it."""

    steam_dirs: tuple[Path, ...]
    cache_file: Path

    @classmethod
    def from_settings(cls, settings: Settings) -> Scanner:
        chosen = (Path(settings.steam_dir),) if settings.steam_dir else ()
        return cls(chosen + DEFAULT_STEAM_DIRS, paths.cache_dir() / "mods.msgpack")

    def __call__(self, ctx: JobContext) -> Library:
        ctx.progress(0, 0, "Finding Stellaris")
        game = find_game(self.steam_dirs)
        return scan_library(ctx, game, self.cache_file)


def scan_library(ctx: JobContext, game: Game, cache_file: Path) -> Library:
    cache = load_msgpack(cache_file, CacheData)
    if cache is None or cache.version != CACHE_VERSION:
        cache = CacheData()

    descriptors = _read_outers(game, cache)
    sources = _mod_sources(game, descriptors)

    mods: dict[str, CachedMod] = {}
    for done, src in enumerate(sources):
        ctx.progress(done, len(sources), "Reading mods")
        entry = read_mod(src, cache.mods.get(src.key))
        mods[entry.mod.key] = entry

    new_cache = CacheData(descriptors=descriptors, mods=mods)
    if new_cache != cache:
        save_msgpack(cache_file, new_cache)

    installed = {key: entry.mod for key, entry in mods.items() if not is_pinned(key)}
    pinned = tuple(entry.mod for key, entry in mods.items() if is_pinned(key))
    dlcs = find_dlcs(game.install_dir)
    problems: list[str] = []
    try:
        launcher = read_launcher(game.launcher_db)
    except LauncherDbError as error:
        launcher = LauncherData(playsets=(), thumbnails={})
        problems.append(str(error))
    _add_launcher_thumbnails(installed, launcher.thumbnails)

    return Library(
        game=game,
        mods=tuple(sorted(installed.values(), key=lambda m: (m.name.casefold(), m.key))),
        pinned=pinned,
        dlcs=dlcs,
        launcher_playsets=tuple(_match_playsets(launcher.playsets, installed, dlcs)),
        launcher_active=next((p.id for p in launcher.playsets if p.active), ""),
        problems=tuple(problems),
        files={key: entry.files for key, entry in mods.items()},
    )


def _read_outers(game: Game, cache: CacheData) -> dict[str, CachedDescriptor]:
    """Every mod/*.mod file the game knows about."""
    found: dict[str, CachedDescriptor] = {}
    try:
        files = sorted(game.mod_dir.glob("*.mod"))
    except OSError:
        return found
    for path in files:
        try:
            found[str(path)] = read_outer(path, cache.descriptors.get(str(path)))
        except OSError:
            continue
    return found


def _mod_sources(game: Game, descriptors: dict[str, CachedDescriptor]) -> list[ModSource]:
    """One source per Workshop folder, plus one per local mod/*.mod file.

    A mod/*.mod file belongs to a Workshop mod when it points into that mod's
    Workshop folder. One we wrote for a pinned copy is that copy. Anything else
    is a local mod.
    """
    workshop: dict[str, Path] = {}
    try:
        for dirent in os.scandir(game.workshop_dir):
            if dirent.name.isdigit() and dirent.is_dir():
                workshop[dirent.name] = Path(dirent.path)
    except OSError:
        pass

    outers: dict[str, tuple[Path, CachedDescriptor]] = {}
    sources: list[ModSource] = []
    for name, entry in descriptors.items():
        outer = Path(name)
        root, archive = _target(game, entry)
        if pinned := _pinned(outer.stem):
            sources.append(ModSource(pinned, "workshop", root, archive, outer, entry))
            continue
        folder = root or (archive.parent if archive else None)
        if folder and folder.parent == game.workshop_dir and folder.name in workshop:
            outers.setdefault(folder.name, (outer, entry))
            continue
        sources.append(ModSource(f"local:{outer.stem}", "local", root, archive, outer, entry))

    for wid, folder in workshop.items():
        if wid in outers:
            outer, entry = outers[wid]
            sources.append(
                ModSource(
                    f"workshop:{wid}", "workshop", folder, _target(game, entry)[1], outer, entry
                )
            )
        else:  # Steam downloaded it, but the launcher hasn't run since
            sources.append(ModSource(f"workshop:{wid}", "workshop", folder, None, None, None))
    return sources


def _pinned(stem: str) -> str | None:
    """The Mod.key of a pinned copy, from its .mod file's name, or None."""
    if not stem.startswith(PIN_PREFIX):
        return None
    wid, _, snapshot = stem.removeprefix(PIN_PREFIX).rpartition("_")
    return pinned_key(f"workshop:{wid}", snapshot) if wid.isdigit() and snapshot else None


def _target(game: Game, entry: CachedDescriptor) -> tuple[Path | None, Path | None]:
    """The folder (`path=`) or zip (`archive=`) a mod/*.mod file points at."""
    desc = entry.descriptor

    def resolve(value: str) -> Path | None:
        if not value:
            return None
        path = Path(value.replace("\\", "/"))
        # Old descriptors use paths relative to the user data folder: "mod/foo".
        return path if path.is_absolute() else game.data_dir / path

    return resolve(desc.path), resolve(desc.archive)


def _match_playsets(
    launcher: tuple[LauncherPlayset, ...], installed: dict[str, Mod], dlcs: tuple[Dlc, ...]
) -> list[Playset]:
    """The launcher's playsets, with each mod matched to its Mod.key.

    A mod that's no longer installed keeps a key and its name, so it can be
    shown as missing instead of being silently dropped.
    """
    by_path = {os.path.normpath(m.root or m.archive): m.key for m in installed.values()}
    playsets: list[Playset] = []
    for ps in launcher:
        entries: list[PlaysetEntry] = []
        for lm in ps.mods:
            key = f"workshop:{lm.steam_id}" if lm.steam_id else ""
            if key not in installed:
                for path in (lm.dir_path, lm.archive_path):
                    if path and os.path.normpath(path) in by_path:
                        key = by_path[os.path.normpath(path)]
                        break
            key = key or f"local:{Path(lm.dir_path or lm.archive_path).stem or lm.name}"
            entries.append(PlaysetEntry(key=key, enabled=lm.enabled, name=lm.name))
        disabled = (launcher_dlc_folder(d, dlcs) for d in ps.disabled_dlcs)
        playsets.append(
            Playset(
                id=ps.id,
                name=ps.name,
                entries=tuple(entries),
                disabled_dlcs=tuple(sorted({d for d in disabled if d})),
                launcher_id=ps.id,
            )
        )
    return playsets


def _add_launcher_thumbnails(installed: dict[str, Mod], thumbnails: dict[str, str]) -> None:
    """Zipped Workshop mods often have no thumbnail of their own, but the launcher
    keeps a copy of the Steam one. Use it when we have nothing better."""
    for steam_id, path in thumbnails.items():
        mod = installed.get(f"workshop:{steam_id}")
        if mod and not mod.picture:
            # The launcher names each copy uniquely, so the path is a fine stamp.
            installed[mod.key] = msgspec.structs.replace(mod, picture=path, picture_stamp=path)
