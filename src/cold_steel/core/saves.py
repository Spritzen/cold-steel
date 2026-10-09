"""Save games: the scan job that finds them, and which playset each belongs to.

    games = SaveScanner.for_game(game)(ctx)
    book = BindingBook.open(path)
    book.bind(["commonwealthofman_1251622081"], playset.id)

A game is one folder, `<empire>_<number>`, in the local `save games/` folder,
in Steam Cloud's, or in both (decision 80). Each save's `meta` is read once,
then cached by size and timestamp (decision 9). Nothing outside our cache file
is written.
"""

import os
from collections.abc import Collection, Iterable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import msgspec

from cold_steel.core.jobs import JobContext
from cold_steel.core.mods import Stamp
from cold_steel.paradox.game import Game
from cold_steel.paradox.save import (
    SaveError,
    SaveInfo,
    cloud_save_dirs,
    local_save_dir,
    read_save_info,
)
from cold_steel.store import paths
from cold_steel.store.files import load_msgpack, save_msgpack
from cold_steel.store.saves import Binding, BindingsFile, load_bindings, save_bindings

# Bump when SaveInfo or CachedSave change shape, so old caches are thrown away.
CACHE_VERSION = 1
SAVE_SUFFIX = ".sav"


class CachedSave(msgspec.Struct, frozen=True):
    stamp: Stamp
    info: SaveInfo | None
    problem: str = ""


class SaveCache(msgspec.Struct):
    version: int = CACHE_VERSION
    saves: dict[str, CachedSave] = msgspec.field(default_factory=dict)


@dataclass(frozen=True)
class Save:
    path: Path
    cloud: bool  # in Steam Cloud's folder, not the local one
    saved: int  # when it was written: its modified time, in nanoseconds
    info: SaveInfo | None  # None when it couldn't be read
    problem: str = ""  # why it couldn't be read

    @property
    def name(self) -> str:
        """The save as the game names it: "2203.01.08", "autosave_2203.01.01"."""
        return self.path.stem


@dataclass(frozen=True)
class SavedGame:
    folder: str  # "commonwealthofman_1251622081", the same in both places
    saves: tuple[Save, ...]  # newest first

    @property
    def newest(self) -> Save:
        return self.saves[0]

    @property
    def info(self) -> SaveInfo | None:
        """What the newest readable save says."""
        return next((s.info for s in self.saves if s.info), None)

    @property
    def empire(self) -> str:
        info = self.info
        return info.empire if info and info.empire else self.folder

    @property
    def saved(self) -> int:
        return self.newest.saved


@dataclass
class SaveScanner:
    """A save scan with its folders. Call it with a JobContext to run it."""

    local_dir: Path
    cloud_dirs: tuple[Path, ...]
    cache_file: Path

    @classmethod
    def for_game(cls, game: Game, cache_file: Path | None = None) -> SaveScanner:
        return cls(
            local_save_dir(game.data_dir),
            cloud_save_dirs(game.steam_dir),
            cache_file or paths.cache_dir() / "saves.msgpack",
        )

    def __call__(self, ctx: JobContext) -> tuple[SavedGame, ...]:
        """Every game, the most recently saved first."""
        ctx.progress(0, 0, "Finding saves")
        found = [(path, stamp, False) for path, stamp in _list_saves(self.local_dir)]
        for folder in self.cloud_dirs:
            found += [(path, stamp, True) for path, stamp in _list_saves(folder)]

        cache = load_msgpack(self.cache_file, SaveCache)
        if cache is None or cache.version != CACHE_VERSION:
            cache = SaveCache()
        entries: dict[str, CachedSave] = {}
        by_game: dict[str, list[Save]] = {}
        for done, (path, stamp, cloud) in enumerate(found):
            ctx.progress(done, len(found), "Reading saves")
            entry = cache.saves.get(str(path))
            if entry is None or entry.stamp != stamp:
                entry = _read(path, stamp)
            entries[str(path)] = entry
            save = Save(path, cloud, stamp[1], entry.info, entry.problem)
            by_game.setdefault(path.parent.name, []).append(save)

        if entries != cache.saves:
            save_msgpack(self.cache_file, SaveCache(saves=entries))

        games = [
            SavedGame(folder, tuple(sorted(saves, key=lambda s: (s.saved, s.name), reverse=True)))
            for folder, saves in by_game.items()
        ]
        return tuple(sorted(games, key=lambda g: (g.saved, g.folder), reverse=True))


def _list_saves(base: Path) -> list[tuple[Path, Stamp]]:
    """Every `<game folder>/<save>.sav` under one save folder, with its stamp."""
    found: list[tuple[Path, Stamp]] = []
    try:
        folders = [Path(d.path) for d in os.scandir(base) if d.is_dir()]
    except OSError:
        return found
    for folder in folders:
        try:
            with os.scandir(folder) as it:
                for dirent in it:
                    if dirent.name.endswith(SAVE_SUFFIX) and dirent.is_file():
                        st = dirent.stat()
                        found.append((Path(dirent.path), (st.st_size, st.st_mtime_ns)))
        except OSError:
            continue
    return found


def _read(path: Path, stamp: Stamp) -> CachedSave:
    try:
        return CachedSave(stamp, read_save_info(path))
    except SaveError as error:
        return CachedSave(stamp, None, str(error))


class BindingBook:
    """Which playset each game belongs to, by game folder. Every change is saved at once."""

    def __init__(self, data: BindingsFile, path: Path) -> None:
        self._data = data
        self._path = path
        self.problems: list[str] = []

    @classmethod
    def open(cls, path: Path) -> BindingBook:
        """A file that exists but can't be read is renamed, not overwritten."""
        data = load_bindings(path)
        problems: list[str] = []
        if data is None and path.exists():
            stamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
            kept = path.with_name(f"{path.stem}.unreadable-{stamp}{path.suffix}")
            path.replace(kept)
            problems.append(f"Your games file couldn't be read. It was kept as {kept.name}.")
        book = cls(data or BindingsFile(), path)
        book.problems = problems
        return book

    def get(self, folder: str) -> Binding | None:
        return self._data.games.get(folder)

    def playset_of(self, folder: str) -> str:
        """The id of the playset the game belongs to, or "" if none."""
        binding = self._data.games.get(folder)
        return binding.playset if binding else ""

    def games_of(self, playset_id: str) -> set[str]:
        return {f for f, b in self._data.games.items() if playset_id and b.playset == playset_id}

    def bind(self, folders: Iterable[str], playset_id: str) -> None:
        """Bind games to a playset, or move them to it from another."""
        bound = datetime.now().strftime("%Y-%m-%d %H:%M")
        for folder in folders:
            self._data.games[folder] = Binding(playset=playset_id, bound=bound)
        self._save()

    def unbind(self, folders: Iterable[str]) -> None:
        """Unbind games by choice. They're never suggested for a playset again."""
        for folder in folders:
            self._data.games[folder] = Binding()
        self._save()

    def forget_playset(self, playset_id: str) -> int:
        """The playset is gone: its games belong to none, and may be suggested
        for another. How many games it had."""
        gone = self.games_of(playset_id)
        for folder in gone:
            del self._data.games[folder]
        if gone:
            self._save()
        return len(gone)

    def _save(self) -> None:
        save_bindings(self._data, self._path)


def bound_games(games: Iterable[SavedGame], book: BindingBook, playset_id: str) -> list[SavedGame]:
    """The games bound to a playset, newest first."""
    return [g for g in games if book.playset_of(g.folder) == playset_id]


def unbound_games(
    games: Iterable[SavedGame], book: BindingBook, playset_ids: Collection[str]
) -> list[SavedGame]:
    """The games bound to no playset that exists, newest first."""
    return [g for g in games if book.playset_of(g.folder) not in playset_ids]
