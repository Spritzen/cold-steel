"""Which mod caused each error in the game's `error.log`.

    report = ErrorReader(library)(ctx)

An error names a file, like `common/traits/x.txt`. The mods the game loaded
are in `dlc_load.json`, in load order. The last of them that has that file is
the one the game was reading. If none has it, the file is the game's own, and
the error goes under "game / unknown" with the errors that name no file.

Some entries only say a mod replaced something: "Object with key: x already
exists, using the one at ...". That's what mods are for, and on a real
24-mod playset it was 84% of the log. Those are marked `override`, so the
window can fold them away (is_override).

Universal Resource Patch and mods like it list resources from many mods, so
any you have show in the top bar. Each one you don't have logs a line, which
is how they're meant to work. Those are marked `resource` and folded away too
(is_missing_resource).

The game also reads every mod/*.mod file as it starts, loaded or not, and
logs problems in them. Those errors name a mod that wasn't loaded, so its
group is marked `loaded=False` and listed after the loaded mods.

A Cold Steel build is one mod holding every file of a playset. Its record
says which mod each file came from, so errors in a build are grouped under
those mods, not under the build.

When the game can't read a file past some point, the objects defined after it
never load, and errors about them show up in other files. A parse error names
those objects (`lost`), so the window can say so.

Many errors name no file, only a thing: "Missing sound effect: x". For a few
known shapes we search the loaded mods' script and graphics files for that
name. If exactly one mod mentions it, the error goes under that mod (`quoted`).
On Cold Steel Mix this placed 75 of the 153 problems that named no file.
"""

import os
import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field, replace
from pathlib import Path

from cold_steel.core.build import BuildRecord, BuiltFile, builds_dir, built_from, load_record
from cold_steel.core.definitions import read_definitions
from cold_steel.core.jobs import JobContext
from cold_steel.core.library import Library
from cold_steel.core.merge_rules import Rules, load_rules
from cold_steel.core.mods import Mod, ModFiles
from cold_steel.paradox.dlc_load import FILE_NAME as DLC_LOAD
from cold_steel.paradox.dlc_load import read_dlc_load
from cold_steel.paradox.error_log import FILE_NAME, LogEntry, read_error_log

GAME = ""  # the group key for errors not traced to a mod
GAME_NAME = "Game / unknown"

# An entry saying one definition replaced another. Each is the start of the
# entry's text, as the game writes it.
_OVERRIDE = re.compile(
    r"Object with key: \S+ already exists"
    r"|an event with id \[[^\]]*\] already exists"
    r"|An item with name \S+ already exists"
    r"|Duplicate of \S+ added to entity system"
    r"|duplicate section template found"
    r"|Variable name \S+ is already taken"
)


# Where the game reads the top bar's resource lists, in interface/resource_groups/.
_RESOURCE_SOURCE = "strategic_resources_gui_group.cpp"

# Entries that name no file but quote something a mod's file mentions: a sound,
# an entity, a texture, a section template. The name is the `name` group.
_QUOTED = tuple(
    re.compile(pattern)
    for pattern in (
        r"Missing sound effect: (?P<name>\S+)",
        r'Couldn\'t find sound effect: "(?P<name>[^"]+)"',
        r'Couldn\'t find particle 3D object "(?P<name>[^"]+)"',
        r"(?P<name>[\w.]+?)\.\w+ uses \[animation = ",
        r"(?P<name>\S+) has no attach point named",
        r"Failed to find texture '(?:[^']*[\\/])?(?P<name>[^'\\/]+)'",
        r"Failed to get section template for key: (?P<name>\S+)",
        r"Missing modifier localization: (?P<name>\S+)",
        r"Missing name localisation for deposit (?P<name>\S+)",
        r"Invalid government species_class reference \[(?P<name>[^\]]+)\]",
    )
)
# Where those names are searched for: the files that refer to such things.
_SEARCHED_FOLDERS = ("common/", "events/", "gfx/", "interface/")
_SEARCHED_SUFFIXES = (".txt", ".asset", ".gfx", ".gui")

# An entry saying the game stopped understanding a file at some line.
_PARSE_BREAK = re.compile(r'Error: "Unexpected token:|Corrupt Event Table Entry')


def is_override(text: str) -> bool:
    """Is this entry only the game saying one definition replaced another?"""
    return _OVERRIDE.match(text) is not None


def is_missing_resource(source: str, text: str) -> bool:
    """Is this entry only a top-bar resource list naming a resource that isn't installed?

    Keyed on the place in the game's code that logs it, not on mod names.
    """
    return source.startswith(_RESOURCE_SOURCE) and text.startswith("Failed to read key reference")


def quoted_name(text: str) -> str:
    """The thing an entry that names no file is about, for the shapes we know; else ""."""
    for pattern in _QUOTED:
        match = pattern.match(text)
        if match:
            return match["name"]
    return ""


@dataclass(frozen=True)
class GameError:
    time: str  # of the first time it was logged
    source: str
    text: str
    file: str = ""  # the file it's about: inside the mod, or the game's
    line: int = 0
    count: int = 1  # how many times this same error was logged
    code: str = ""  # the text of `line` in `file`, if it could be read
    override: bool = False  # only says one definition replaced another (is_override)
    resource: bool = False  # only a top-bar resource that isn't installed (is_missing_resource)
    # A parse error: the objects defined after `line` in `file`, lost if the game
    # stopped reading there.
    lost: tuple[str, ...] = ()
    # It named no file. `file` is the only mod file that mentions this name.
    quoted: str = ""

    @property
    def normal(self) -> bool:
        """Folded away by default: it doesn't mean anything is wrong."""
        return self.override or self.resource


@dataclass(frozen=True)
class ErrorGroup:
    key: str  # a Mod.key, or GAME
    name: str
    errors: tuple[GameError, ...]
    loaded: bool = True  # False for a mod the game didn't load in this run

    @property
    def total(self) -> int:
        return sum(e.count for e in self.errors)

    @property
    def overrides(self) -> int:
        return sum(e.count for e in self.errors if e.override)

    @property
    def resources(self) -> int:
        return sum(e.count for e in self.errors if e.resource)


@dataclass(frozen=True)
class ErrorReport:
    log_file: Path
    written: float = 0.0  # when the log was written; 0 if there's no log
    groups: tuple[ErrorGroup, ...] = ()  # most errors first; the game's group last
    # The mod list changed after this run, so some errors may be matched to the wrong mod.
    stale: bool = False
    build: str = ""  # the name of a loaded build whose errors were traced to its mods

    @property
    def total(self) -> int:
        return sum(g.total for g in self.groups)

    @property
    def overrides(self) -> int:
        return sum(g.overrides for g in self.groups)

    @property
    def resources(self) -> int:
        return sum(g.resources for g in self.groups)

    @property
    def problems(self) -> int:
        """Everything but the overrides and missing resources."""
        return self.total - self.overrides - self.resources


@dataclass
class ErrorReader:
    """Reads error.log and groups its errors by mod. Call it with a JobContext."""

    library: Library
    builds: Path = field(default_factory=builds_dir)  # where build records are

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
        builds = self._build_records(loaded)
        by_descriptor = _by_descriptor(self.library.every_mod)
        by_folder = {
            os.path.normpath(m.root or m.archive): m for m in self.library.every_mod if m.installed
        }

        def traced(mod: Mod | None, file: str) -> tuple[str, str]:
            """The group key and file name: in a build, the mod the file came from."""
            if mod is not None and mod.key in builds:
                built = builds[mod.key].files.get(file.casefold())
                if built is not None:
                    return built.layer, built.source
            return (mod.key if mod else GAME), file

        quoted = self._quoted(ctx, entries, index, traced)

        grouped: dict[str, dict[tuple[str, str], GameError]] = {}
        for done, entry in enumerate(entries):
            if done % 500 == 0:
                ctx.progress(done, len(entries), "Matching errors to mods")
            mod, file = self._owner(entry, index, by_descriptor, by_folder)
            line, name = entry.line, ""
            if mod is None and not entry.file:
                name = quoted_name(entry.text)
                if name in quoted:
                    mod, file, line = quoted[name]
                else:
                    name = ""
            key, shown = traced(mod, file)
            same = grouped.setdefault(key, {})
            seen = same.get((entry.source, entry.text))
            if seen:
                same[(entry.source, entry.text)] = replace(seen, count=seen.count + 1)
                continue
            parse_break = _PARSE_BREAK.match(entry.text) is not None
            same[(entry.source, entry.text)] = GameError(
                entry.time,
                entry.source,
                entry.text,
                shown,
                line,
                code=index.code(mod, file, line),
                override=is_override(entry.text),
                resource=is_missing_resource(entry.source, entry.text),
                lost=index.defined_after(mod, file, line) if parse_break else (),
                quoted=name,
            )

        names = {m.key: m.name for m in self.library.every_mod}
        loaded_keys = {m.key for m in loaded}
        for build in builds.values():
            names = build.record.names | names
            loaded_keys.update(build.record.order)
        groups = [
            ErrorGroup(
                key,
                names.get(key, GAME_NAME),
                tuple(errors.values()),
                loaded=key == GAME or key in loaded_keys,
            )
            for key, errors in grouped.items()
        ]
        # Loaded mods, most errors first; then mods that weren't loaded; then the game.
        groups.sort(key=lambda g: (g.key == GAME, not g.loaded, -g.total, g.name.casefold()))
        try:
            stale = (game.data_dir / DLC_LOAD).stat().st_mtime > written
        except OSError:
            stale = False
        # A build rebuilt after the run may have taken some files from other mods.
        stale = stale or any(b.saved > written for b in builds.values())
        build_names = ", ".join(names.get(k, k) for k in builds)
        return ErrorReport(log_file, written, tuple(groups), stale, build_names)

    @staticmethod
    def _quoted(
        ctx: JobContext,
        entries: list[LogEntry],
        index: _FileIndex,
        traced: Callable[[Mod | None, str], tuple[str, str]],
    ) -> dict[str, tuple[Mod, str, int]]:
        """For each name an entry without a file quotes: the one place it's
        mentioned, if only one mod mentions it."""
        names = {
            name
            for e in entries
            if not e.file and not is_override(e.text) and (name := quoted_name(e.text))
        }
        if not names:
            return {}
        ctx.progress(0, 0, "Searching mods for the names errors quote")
        found: dict[str, tuple[Mod, str, int]] = {}
        for name, places in index.mentions(names).items():
            if len({traced(mod, file)[0] for mod, file, _ in places}) == 1:
                found[name] = places[0]
        return found

    def _build_records(self, loaded: Iterable[Mod]) -> dict[str, _Build]:
        """The record of each loaded build, by the build's Mod.key."""
        builds: dict[str, _Build] = {}
        for mod in loaded:
            playset_id = built_from(mod.key)
            if playset_id is None:
                continue
            record = load_record(self.builds, playset_id)
            if record is None:
                continue
            try:
                saved = (self.builds / f"{playset_id}.json").stat().st_mtime
            except OSError:
                saved = 0.0
            files = {path.casefold(): built for path, built in record.files.items()}
            builds[mod.key] = _Build(record, files, saved)
        return builds

    def _loaded_mods(self) -> list[Mod]:
        """The mods in dlc_load.json, in load order."""
        load = read_dlc_load(self.library.game.data_dir)
        if load is None:
            return []
        by_descriptor = _by_descriptor(self.library.every_mod)
        by_key = {m.key: m for m in self.library.every_mod}
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


@dataclass(frozen=True)
class _Build:
    record: BuildRecord
    files: dict[str, BuiltFile]  # by path in the build, casefolded
    saved: float  # when the record was written


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
    _defined: dict[tuple[str, str], tuple[tuple[int, str], ...]] = field(default_factory=dict)
    _rules: Rules | None = None

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

    def mentions(self, names: Iterable[str]) -> dict[str, list[tuple[Mod, str, int]]]:
        """Where the files the game read mention each name, as a whole word:
        (mod, file, line), the first mention in each file. Only script and
        graphics text files are searched."""
        words = sorted(names, key=len, reverse=True)
        pattern = re.compile(
            rb"(?<!\w)(?:" + b"|".join(re.escape(w.encode()) for w in words) + rb")(?!\w)"
        )
        by_mod: dict[str, list[str]] = {}
        mods: dict[str, Mod] = {}
        for mod, name in self._owners.values():
            lowered = name.lower()
            if lowered.startswith(_SEARCHED_FOLDERS) and lowered.endswith(_SEARCHED_SUFFIXES):
                by_mod.setdefault(mod.key, []).append(name)
                mods[mod.key] = mod
        found: dict[str, list[tuple[Mod, str, int]]] = {}
        for key, files in by_mod.items():
            mod = mods[key]
            with ModFiles(mod, self.library.files.get(key, {})) as opened:
                for file in files:
                    data = opened.read(file) or b""
                    seen: set[bytes] = set()
                    for match in pattern.finditer(data):
                        word = match.group()
                        if word in seen:
                            continue
                        seen.add(word)
                        line = data.count(b"\n", 0, match.start()) + 1
                        found.setdefault(word.decode(), []).append((mod, file, line))
        return found

    def code(self, mod: Mod | None, file: str, line: int) -> str:
        """The text of one line of a file, from the mod or from the game."""
        if line <= 0 or not file:
            return ""
        key = (mod.key if mod else GAME, file)
        if key not in self._lines:
            data = self._read(mod, file)
            self._lines[key] = data.decode("utf-8-sig", errors="replace").splitlines()
        lines = self._lines[key]
        return lines[line - 1].strip()[:200] if line <= len(lines) else ""

    def defined_after(self, mod: Mod | None, file: str, line: int) -> tuple[str, ...]:
        """The names of the objects a script file defines after `line`."""
        if line <= 0 or not file:
            return ()
        key = (mod.key if mod else GAME, file)
        if key not in self._defined:
            self._rules = self._rules or load_rules()
            rule = self._rules.for_file(file)
            if rule.unit in ("file", "localisation"):
                self._defined[key] = ()
            else:
                found = read_definitions(file, self._read(mod, file), rule, "")
                self._defined[key] = tuple((d.line, d.key) for d in found)
        return tuple(name for start, name in self._defined[key] if start > line)

    def _read(self, mod: Mod | None, file: str) -> bytes:
        if mod is None:
            game_dir = self.library.game.install_dir
            path = game_dir / file
            try:
                if not path.resolve().is_relative_to(game_dir.resolve()):
                    return b""
                data: bytes | None = path.read_bytes()
            except OSError:
                return b""
        elif mod.descriptor_file and file == Path(mod.descriptor_file).name:
            try:
                data = Path(mod.descriptor_file).read_bytes()
            except OSError:
                return b""
        else:
            with ModFiles(mod, self.library.files.get(mod.key, {})) as files:
                data = files.read(file)
        return data or b""
