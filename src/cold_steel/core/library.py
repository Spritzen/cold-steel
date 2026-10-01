"""The scan job: find the game, every mod and the launcher's playsets.

    library = Scanner(steam_dirs, cache_file)(ctx)

Unchanged mods come from the cache (decision 9). Nothing outside our cache
file is written.

Mods are read one after another. Checking timestamps is Python work that holds
the GIL, so threads made it slower (0.28 s alone, 0.42 s with 8 threads, on
55 mods / 72,000 files). Phase 4's parsing is where parallel work pays.
"""

import os
from dataclasses import dataclass, field
from pathlib import Path

import msgspec

from cold_steel.core.jobs import JobContext
from cold_steel.core.mods import (
    CachedDescriptor,
    CachedMod,
    Mod,
    ModSource,
    read_mod,
    read_outer,
)
from cold_steel.core.version import is_outdated
from cold_steel.paradox.game import DEFAULT_STEAM_DIRS, Game, find_game
from cold_steel.paradox.launcher_db import (
    LauncherData,
    LauncherDbError,
    LauncherPlayset,
    read_launcher,
)
from cold_steel.store import paths
from cold_steel.store.files import load_msgpack, save_msgpack
from cold_steel.store.settings import Settings

# Bump when Mod or CachedMod change shape, so old caches are thrown away.
CACHE_VERSION = 1


class CacheData(msgspec.Struct):
    version: int = CACHE_VERSION
    descriptors: dict[str, CachedDescriptor] = msgspec.field(default_factory=dict)
    mods: dict[str, CachedMod] = msgspec.field(default_factory=dict)


@dataclass(frozen=True)
class PlaysetEntry:
    key: str  # a Mod.key, from Library.mods or Library.missing
    enabled: bool


@dataclass(frozen=True)
class Playset:
    id: str
    name: str
    active: bool
    entries: tuple[PlaysetEntry, ...]  # in load order


@dataclass(frozen=True)
class Library:
    game: Game
    mods: tuple[Mod, ...]  # installed mods, by name
    missing: tuple[Mod, ...] = ()  # in a playset but no longer installed
    playsets: tuple[Playset, ...] = ()
    problems: tuple[str, ...] = ()  # things that stopped part of the scan
    _outdated: dict[str, bool] = field(default_factory=dict, compare=False, repr=False)

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

    installed = {key: entry.mod for key, entry in mods.items()}
    problems: list[str] = []
    try:
        launcher = read_launcher(game.launcher_db)
    except LauncherDbError as error:
        launcher = LauncherData(playsets=(), thumbnails={})
        problems.append(str(error))
    playsets, missing = _match_playsets(launcher.playsets, installed)
    _add_launcher_thumbnails(installed, launcher.thumbnails)

    return Library(
        game=game,
        mods=tuple(sorted(installed.values(), key=lambda m: (m.name.casefold(), m.key))),
        missing=tuple(missing.values()),
        playsets=tuple(playsets),
        problems=tuple(problems),
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
    Workshop folder. Anything else is a local mod.
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
    launcher: tuple[LauncherPlayset, ...], installed: dict[str, Mod]
) -> tuple[list[Playset], dict[str, Mod]]:
    by_path = {os.path.normpath(m.root or m.archive): m.key for m in installed.values()}
    missing: dict[str, Mod] = {}
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
            if key not in installed:
                key = key or f"local:{lm.dir_path or lm.archive_path or lm.name}"
                missing.setdefault(
                    key,
                    Mod(
                        key=key,
                        source="workshop" if lm.steam_id else "local",
                        name=lm.name or key,
                        remote_file_id=lm.steam_id,
                        installed=False,
                    ),
                )
            entries.append(PlaysetEntry(key=key, enabled=lm.enabled))
        playsets.append(Playset(ps.id, ps.name, ps.active, tuple(entries)))
    return playsets, missing


def _add_launcher_thumbnails(installed: dict[str, Mod], thumbnails: dict[str, str]) -> None:
    """Zipped Workshop mods often have no thumbnail of their own, but the launcher
    keeps a copy of the Steam one. Use it when we have nothing better."""
    for steam_id, path in thumbnails.items():
        mod = installed.get(f"workshop:{steam_id}")
        if mod and not mod.picture:
            # The launcher names each copy uniquely, so the path is a fine stamp.
            installed[mod.key] = msgspec.structs.replace(mod, picture=path, picture_stamp=path)
