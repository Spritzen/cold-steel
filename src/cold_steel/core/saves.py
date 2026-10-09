"""Saves: the scan job that finds them, and which playset each belongs to.

    saves = SaveScanner.for_game(game)(ctx)
    book = BindingBook.open(path)
    book.bind(["commonwealthofman_1251622081"], playset.id)

A save is one folder, `<empire>_<number>`, in the local `save games/` folder,
in Steam Cloud's, or in both (decision 80). Saves hidden while the game runs
(core/hide.py) are read too, as local ones. Each `.sav` file in it is one save
file of that save. A save file's `meta` is read once, then cached by size and
timestamp (decision 9). Nothing outside our cache file is written.
"""

import os
from collections.abc import Collection, Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import msgspec

from cold_steel.core.hide import hidden_dir
from cold_steel.core.jobs import JobContext
from cold_steel.core.library import Library
from cold_steel.core.mods import Stamp
from cold_steel.core.playsets import as_played
from cold_steel.core.version import is_outdated
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
from cold_steel.store.playsets import Playset
from cold_steel.store.saves import Binding, BindingsFile, load_bindings, save_bindings

# Bump when SaveInfo or CachedFile change shape, so old caches are thrown away.
CACHE_VERSION = 1
SAVE_SUFFIX = ".sav"


class CachedFile(msgspec.Struct, frozen=True):
    stamp: Stamp
    info: SaveInfo | None
    problem: str = ""


class SaveCache(msgspec.Struct):
    version: int = CACHE_VERSION
    files: dict[str, CachedFile] = msgspec.field(default_factory=dict)  # by path


@dataclass(frozen=True)
class SaveFile:
    path: Path
    cloud: bool  # in Steam Cloud's folder, not the local one
    saved: int  # when it was written: its modified time, in nanoseconds
    info: SaveInfo | None  # None when it couldn't be read
    problem: str = ""  # why it couldn't be read

    @property
    def name(self) -> str:
        """The file as the game names it: "2203.01.08", "autosave_2203.01.01"."""
        return self.path.stem


@dataclass(frozen=True)
class Save:
    folder: str  # "commonwealthofman_1251622081", the same in both places
    files: tuple[SaveFile, ...]  # newest first

    @property
    def newest(self) -> SaveFile:
        return self.files[0]

    @property
    def info(self) -> SaveInfo | None:
        """What the newest readable file says."""
        return next((f.info for f in self.files if f.info), None)

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
    hidden_dir: Path | None = None  # saves of other playsets, hidden while the game runs

    @classmethod
    def for_game(cls, game: Game, cache_file: Path | None = None) -> SaveScanner:
        return cls(
            local_save_dir(game.data_dir),
            cloud_save_dirs(game.steam_dir),
            cache_file or paths.cache_dir() / "saves.msgpack",
            hidden_dir(game.data_dir),
        )

    def __call__(self, ctx: JobContext) -> tuple[Save, ...]:
        """Every save, the most recently written first."""
        ctx.progress(0, 0, "Finding saves")
        found = [(path, stamp, False) for path, stamp in _list_files(self.local_dir)]
        if self.hidden_dir is not None:
            found += [(path, stamp, False) for path, stamp in _list_files(self.hidden_dir)]
        for folder in self.cloud_dirs:
            found += [(path, stamp, True) for path, stamp in _list_files(folder)]

        cache = load_msgpack(self.cache_file, SaveCache)
        if cache is None or cache.version != CACHE_VERSION:
            cache = SaveCache()
        entries: dict[str, CachedFile] = {}
        by_folder: dict[str, list[SaveFile]] = {}
        for done, (path, stamp, cloud) in enumerate(found):
            ctx.progress(done, len(found), "Reading saves")
            entry = cache.files.get(str(path))
            if entry is None or entry.stamp != stamp:
                entry = _read(path, stamp)
            entries[str(path)] = entry
            file = SaveFile(path, cloud, stamp[1], entry.info, entry.problem)
            by_folder.setdefault(path.parent.name, []).append(file)

        if entries != cache.files:
            save_msgpack(self.cache_file, SaveCache(files=entries))

        saves = [
            Save(folder, tuple(sorted(files, key=lambda f: (f.saved, f.name), reverse=True)))
            for folder, files in by_folder.items()
        ]
        return tuple(sorted(saves, key=lambda s: (s.saved, s.folder), reverse=True))


def _list_files(base: Path) -> list[tuple[Path, Stamp]]:
    """Every `<save folder>/<file>.sav` under one save games folder, with its stamp."""
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


def _read(path: Path, stamp: Stamp) -> CachedFile:
    try:
        return CachedFile(stamp, read_save_info(path))
    except SaveError as error:
        return CachedFile(stamp, None, str(error))


class BindingBook:
    """Which playset each save belongs to, by save folder. Every change is saved at once."""

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
            problems.append(f"Your saves file couldn't be read. It was kept as {kept.name}.")
        book = cls(data or BindingsFile(), path)
        book.problems = problems
        return book

    def get(self, folder: str) -> Binding | None:
        return self._data.saves.get(folder)

    def playset_of(self, folder: str) -> str:
        """The id of the playset the save belongs to, or "" if none."""
        binding = self._data.saves.get(folder)
        return binding.playset if binding else ""

    def saves_of(self, playset_id: str) -> set[str]:
        return {f for f, b in self._data.saves.items() if playset_id and b.playset == playset_id}

    def bound_elsewhere(self, playset_id: str, playset_ids: Collection[str]) -> set[str]:
        """Saves bound to a playset other than this one, among `playset_ids`,
        the playsets that exist. Play hides these from the game."""
        return {
            f
            for f, b in self._data.saves.items()
            if b.playset in playset_ids and b.playset != playset_id
        }

    def bind(self, folders: Iterable[str], playset_id: str) -> None:
        """Bind saves to a playset, or move them to it from another."""
        bound = datetime.now().strftime("%Y-%m-%d %H:%M")
        for folder in folders:
            self._data.saves[folder] = Binding(playset=playset_id, bound=bound)
        self._save()

    def keep(self, folders: Iterable[str], build: str) -> None:
        """Keep saves with their built playset's new build (BuildRecord.built),
        so they aren't marked as made with an older build."""
        for folder in folders:
            if binding := self._data.saves.get(folder):
                self._data.saves[folder] = msgspec.structs.replace(binding, build=build)
        self._save()

    def unbind(self, folders: Iterable[str]) -> None:
        """Unbind saves by choice. They're never suggested for a playset again."""
        for folder in folders:
            self._data.saves[folder] = Binding()
        self._save()

    def forget_playset(self, playset_id: str) -> int:
        """The playset is gone: its saves belong to none, and may be suggested
        for another. How many saves it had."""
        gone = self.saves_of(playset_id)
        for folder in gone:
            del self._data.saves[folder]
        if gone:
            self._save()
        return len(gone)

    def _save(self) -> None:
        save_bindings(self._data, self._path)


def bound_saves(saves: Iterable[Save], book: BindingBook, playset_id: str) -> list[Save]:
    """The saves bound to a playset, newest first."""
    return [s for s in saves if book.playset_of(s.folder) == playset_id]


def unbound_saves(
    saves: Iterable[Save], book: BindingBook, playset_ids: Collection[str]
) -> list[Save]:
    """The saves bound to no playset that exists, newest first."""
    return [s for s in saves if book.playset_of(s.folder) not in playset_ids]


# Binding on their own, and how a save compares with its playset now


def played_mods(playset: Playset, library: Library) -> tuple[str, ...]:
    """The mods the game loads for a playset, by name, in load order: what a
    save file made with it lists. Pinned copies keep their mod's name."""
    names = {m.key: m.name for m in library.every_mod}
    return tuple(names[e.key] for e in as_played(playset).entries if e.enabled and e.key in names)


def played_since(saves: Iterable[Save], book: BindingBook, since: int) -> list[str]:
    """Saves with a file written since `since` (nanoseconds) and no binding yet.
    After Play, these are bound to the playset that was played (decision 82)."""
    return [s.folder for s in saves if s.saved >= since and book.get(s.folder) is None]


def suggest(
    saves: Iterable[Save], book: BindingBook, played: Mapping[str, tuple[str, ...]]
) -> dict[str, str]:
    """Save folder -> the id of the one playset whose mods, as played (`played`,
    by playset id), are exactly its newest file's. Only for saves never bound,
    or bound to a playset that's gone. When several playsets match, as copies
    of one playset do, none is suggested."""
    by_mods: dict[tuple[str, ...], list[str]] = {}
    for playset_id, mods in played.items():
        by_mods.setdefault(mods, []).append(playset_id)
    found: dict[str, str] = {}
    for save in saves:
        binding = book.get(save.folder)
        if binding is not None and (not binding.playset or binding.playset in played):
            continue  # unbound by choice, or bound
        info = save.info
        matches = by_mods.get(info.mods, []) if info else []
        if len(matches) == 1:
            found[save.folder] = matches[0]
    return found


def compare_mods(
    before: Sequence[str], now: Sequence[str]
) -> tuple[tuple[str, ...], tuple[str, ...], bool]:
    """Mods added since, mods removed since, and whether the same mods are now
    in another order. Both are mod names in load order."""
    was, is_ = set(before), set(now)
    added = tuple(m for m in now if m not in was)
    removed = tuple(m for m in before if m not in is_)
    return added, removed, not added and not removed and tuple(now) != tuple(before)


@dataclass(frozen=True)
class SaveCheck:
    """How a save's newest file compares with its playset now."""

    added: tuple[str, ...] = ()  # mods the playset has that the file doesn't
    removed: tuple[str, ...] = ()  # mods the file has that the playset doesn't any more
    reordered: bool = False  # the same mods, in another order
    older_build: bool = False  # the build was redone since, and not accepted for this save
    patch_changed: bool = False  # the patch mod was generated again since
    older_game: bool = False  # written by an older game version (major.minor)

    @property
    def mods_differ(self) -> bool:
        return bool(self.added or self.removed or self.reordered)

    @property
    def marks(self) -> tuple[str, ...]:
        """ "Mods differ", "Older build", "Patch changed", "Older game": each that applies."""
        return tuple(
            text
            for on, text in (
                (self.mods_differ, "Mods differ"),
                (self.older_build, "Older build"),
                (self.patch_changed, "Patch changed"),
                (self.older_game, "Older game"),
            )
            if on
        )

    def details(self) -> list[str]:
        """One sentence per mark."""
        lines: list[str] = []
        if self.added:
            lines.append("Added since: " + ", ".join(self.added))
        if self.removed:
            lines.append("Removed since: " + ", ".join(self.removed))
        if self.reordered:
            lines.append("The same mods, in another load order")
        if self.older_build:
            lines.append("The playset was built again after this save's newest file")
        if self.patch_changed:
            lines.append("The patch mod was generated again after this save's newest file")
        if self.older_game:
            lines.append("Written by an older version of the game")
        return lines


def check_save(
    save: Save,
    mods: Sequence[str],
    game_version: str,
    *,
    build: str = "",
    patch_written: int = 0,
) -> SaveCheck:
    """Compare a save's newest readable file with its playset now.

    `mods` is the playset as played (played_mods). `build` is when the built
    mod it plays was last built (BuildRecord.built), for a built playset, and
    "" once the save was kept with that build. `patch_written` is when its
    patch mod was last generated, in nanoseconds.
    """
    file = next((f for f in save.files if f.info), None)
    if file is None or file.info is None:
        return SaveCheck()
    info = file.info
    added, removed, reordered = compare_mods(info.mods, mods)
    return SaveCheck(
        added=added,
        removed=removed,
        reordered=reordered,
        older_build=bool(build) and file.saved < _nanoseconds(build),
        patch_changed=patch_written > file.saved,
        older_game=is_outdated(info.version.rsplit(" ", 1)[-1], game_version),
    )


def _nanoseconds(stamp: str) -> int:
    """A time as BuildRecord.built has it, "2026-10-09 14:03". 0 if unreadable."""
    try:
        return int(datetime.strptime(stamp, "%Y-%m-%d %H:%M").timestamp() * 1e9)
    except ValueError:
        return 0
