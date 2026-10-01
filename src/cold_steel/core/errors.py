"""Which mod caused each error in the game's `error.log`.

    report = ErrorReader(library)(ctx)

An error names a file, like `common/traits/x.txt`. The mods the game loaded
are in `dlc_load.json`, in load order. The last of them that has that file is
the one the game was reading. If none has it, the file is the game's own, and
the error goes under "game / unknown" with the errors that name no file.
"""

import os
from collections.abc import Iterable
from dataclasses import dataclass, field, replace
from pathlib import Path

from cold_steel.core.jobs import JobContext
from cold_steel.core.library import Library
from cold_steel.core.mods import Mod, ModFiles
from cold_steel.paradox.dlc_load import FILE_NAME as DLC_LOAD
from cold_steel.paradox.dlc_load import read_dlc_load
from cold_steel.paradox.error_log import FILE_NAME, LogEntry, read_error_log

GAME = ""  # the group key for errors not traced to a mod
GAME_NAME = "Game / unknown"


@dataclass(frozen=True)
class GameError:
    time: str  # of the first time it was logged
    source: str
    text: str
    file: str = ""  # the file it's about: inside the mod, or the game's
    line: int = 0
    count: int = 1  # how many times this same error was logged
    code: str = ""  # the text of `line` in `file`, if it could be read


@dataclass(frozen=True)
class ErrorGroup:
    key: str  # a Mod.key, or GAME
    name: str
    errors: tuple[GameError, ...]

    @property
    def total(self) -> int:
        return sum(e.count for e in self.errors)


@dataclass(frozen=True)
class ErrorReport:
    log_file: Path
    written: float = 0.0  # when the log was written; 0 if there's no log
    groups: tuple[ErrorGroup, ...] = ()  # most errors first; the game's group last
    # The mod list changed after this run, so some errors may be matched to the wrong mod.
    stale: bool = False

    @property
    def total(self) -> int:
        return sum(g.total for g in self.groups)


@dataclass
class ErrorReader:
    """Reads error.log and groups its errors by mod. Call it with a JobContext."""

    library: Library

    def __call__(self, ctx: JobContext) -> ErrorReport:
        game = self.library.game
        log_file = game.data_dir / FILE_NAME
        ctx.progress(0, 0, "Reading error.log")
        try:
            written = log_file.stat().st_mtime
            entries = read_error_log(game.data_dir)
        except OSError:
            return ErrorReport(log_file)

        loaded = self._loaded_mods()
        index = _FileIndex(loaded, self.library)
        by_descriptor = _by_descriptor(self.library.mods)
        by_folder = {
            os.path.normpath(m.root or m.archive): m for m in self.library.mods if m.installed
        }

        grouped: dict[str, dict[tuple[str, str], GameError]] = {}
        for done, entry in enumerate(entries):
            if done % 500 == 0:
                ctx.progress(done, len(entries), "Matching errors to mods")
            mod, file = self._owner(entry, index, by_descriptor, by_folder)
            key = mod.key if mod else GAME
            same = grouped.setdefault(key, {})
            seen = same.get((entry.source, entry.text))
            if seen:
                same[(entry.source, entry.text)] = replace(seen, count=seen.count + 1)
            else:
                same[(entry.source, entry.text)] = GameError(
                    entry.time,
                    entry.source,
                    entry.text,
                    file,
                    entry.line,
                    code=index.code(mod, file, entry.line),
                )

        names = {m.key: m.name for m in self.library.mods}
        groups = [
            ErrorGroup(key, names.get(key, GAME_NAME), tuple(errors.values()))
            for key, errors in grouped.items()
        ]
        groups.sort(key=lambda g: (g.key == GAME, -g.total, g.name.casefold()))
        try:
            stale = (game.data_dir / DLC_LOAD).stat().st_mtime > written
        except OSError:
            stale = False
        return ErrorReport(log_file, written, tuple(groups), stale)

    def _loaded_mods(self) -> list[Mod]:
        """The mods in dlc_load.json, in load order."""
        load = read_dlc_load(self.library.game.data_dir)
        if load is None:
            return []
        by_descriptor = _by_descriptor(self.library.mods)
        by_key = {m.key: m for m in self.library.mods}
        mods: list[Mod] = []
        for entry in load.enabled_mods:
            name = Path(entry).name
            mod = by_descriptor.get(name)
            if mod is None and name.startswith("ugc_"):
                # Play may have just written this .mod file; the scan hasn't seen it.
                mod = by_key.get(f"workshop:{name.removeprefix('ugc_').removesuffix('.mod')}")
            if mod is not None and mod.installed:
                mods.append(mod)
        return mods

    @staticmethod
    def _owner(
        entry: LogEntry,
        index: _FileIndex,
        by_descriptor: dict[str, Mod],
        by_folder: dict[str, Mod],
    ) -> tuple[Mod | None, str]:
        """The mod an entry is about, and its file's real name inside that mod."""
        file = entry.file.replace("\\", "/")
        if not file:
            return None, ""
        if file.startswith("/"):  # inside a mod's folder, or the folder itself
            path = os.path.normpath(file)
            for folder, mod in by_folder.items():
                if path == folder or path.startswith(folder + os.sep):
                    return mod, os.path.relpath(path, folder) if path != folder else ""
            return None, file
        if file.startswith("mod/") and file.endswith(".mod"):
            name = Path(file).name
            if name in by_descriptor:
                return by_descriptor[name], name
        return index.owner(file)


def _by_descriptor(mods: Iterable[Mod]) -> dict[str, Mod]:
    """Mods by the name of their mod/*.mod file."""
    return {Path(m.descriptor_file).name: m for m in mods if m.descriptor_file}


@dataclass
class _FileIndex:
    """Which loaded mod the game took each file from: the last one that has it."""

    mods: list[Mod]
    library: Library
    _owners: dict[str, tuple[Mod, str]] = field(default_factory=dict)
    _lines: dict[tuple[str, str], list[str]] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for mod in self.mods:  # later mods replace earlier ones
            for name in self._names(mod):
                self._owners[name.casefold()] = (mod, name)

    def _names(self, mod: Mod) -> Iterable[str]:
        with ModFiles(mod, self.library.files.get(mod.key, {})) as files:
            return files.names()

    def owner(self, file: str) -> tuple[Mod | None, str]:
        found = self._owners.get(file.removeprefix("./").casefold())
        return found if found else (None, file)

    def code(self, mod: Mod | None, file: str, line: int) -> str:
        """The text of one line of a file, from the mod or from the game."""
        if line <= 0 or not file:
            return ""
        key = (mod.key if mod else GAME, file)
        if key not in self._lines:
            self._lines[key] = self._read_lines(mod, file)
        lines = self._lines[key]
        return lines[line - 1].strip()[:200] if line <= len(lines) else ""

    def _read_lines(self, mod: Mod | None, file: str) -> list[str]:
        if mod is None:
            game_dir = self.library.game.install_dir
            path = game_dir / file
            try:
                if not path.resolve().is_relative_to(game_dir.resolve()):
                    return []
                data: bytes | None = path.read_bytes()
            except OSError:
                return []
        elif mod.descriptor_file and file == Path(mod.descriptor_file).name:
            try:
                data = Path(mod.descriptor_file).read_bytes()
            except OSError:
                return []
        else:
            with ModFiles(mod, self.library.files.get(mod.key, {})) as files:
                data = files.read(file)
        if data is None:
            return []
        return data.decode("utf-8-sig", errors="replace").splitlines()
