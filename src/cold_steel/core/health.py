"""Health checks: problems in a mod that make the game quietly ignore part of it.

    health = HealthChecker(library, cache_file)(ctx)
    health["workshop:123"]  -> (Issue, ...), errors and warnings mixed

File checks are cached per mod, and only redone when one of its files changed
size or timestamp (decision 9). Descriptor checks are cheap and depend on the
game version, so they run every time.

What each check looks for, and why, is in docs/phases/phase-3-diagnose.md.
The rules were tested against the game's own files, which pass every one.
"""

import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import msgspec
import xxhash

from cold_steel.core.jobs import JobContext
from cold_steel.core.library import Library
from cold_steel.core.mods import Mod, ModFiles, Stamp
from cold_steel.core.version import is_outdated
from cold_steel.store.files import load_msgpack, save_msgpack

# Bump when a check changes, so cached results are thrown away.
CHECKS_VERSION = 1

type Severity = Literal["error", "warning"]
type Status = Literal["ok", "warning", "error"]


class Issue(msgspec.Struct, frozen=True):
    severity: Severity
    text: str  # what's wrong and what it does in-game, in plain words
    file: str = ""  # path inside the mod, or the mod/*.mod file's name
    line: int = 0
    detail: str = ""  # the line itself, when seeing it helps


type Health = tuple[Issue, ...]


def status(issues: Health) -> Status:
    if any(i.severity == "error" for i in issues):
        return "error"
    return "warning" if issues else "ok"


# Localisation

LOC_FOLDERS = ("localisation/", "localisation_synced/")
BOM = b"\xef\xbb\xbf"
# Most problems in one file are the same mistake repeated. Show this many lines.
MAX_LINES_PER_FILE = 20

# The `:0` is optional: the game's own files leave it out on most lines.
_LANGUAGE = re.compile(r"l_\w+\s*:\s*\d*\s*(#.*)?")
_ENTRY = re.compile(r'[^\s:#"]+\s*:\s*\d*\s*".*"\s*(#.*)?')


def check_localisation(name: str, data: bytes) -> list[Issue]:
    if not data.startswith(BOM):
        return [
            Issue(
                "error",
                "No UTF-8 BOM at the start, so the game ignores this whole file. "
                "Its text shows as raw keys.",
                name,
            )
        ]
    issues: list[Issue] = []
    has_language = False
    bad = 0
    for number, raw in enumerate(data[len(BOM) :].decode("utf-8", "replace").splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if _LANGUAGE.fullmatch(line):
            has_language = True
            continue
        if not has_language:
            has_language = True  # say so once
            issues.append(
                Issue(
                    "error",
                    'The file doesn\'t start with a language line like "l_english:", '
                    "so the game ignores it.",
                    name,
                    number,
                    line[:120],
                )
            )
            if not _ENTRY.fullmatch(line):
                continue
        if not _ENTRY.fullmatch(line):
            bad += 1
            if bad <= MAX_LINES_PER_FILE:
                issues.append(
                    Issue(
                        "error",
                        'This line isn\'t KEY: "text", so the game skips it.',
                        name,
                        number,
                        line[:120],
                    )
                )
    if bad > MAX_LINES_PER_FILE:
        more = bad - MAX_LINES_PER_FILE
        issues.append(Issue("error", f"And {more} more lines like that.", name))
    return issues


# Script

SCRIPT_SUFFIXES = (".txt", ".gui", ".gfx", ".asset")
# Top-level folders the game reads script from. A .txt anywhere else is a readme.
SCRIPT_FOLDERS = frozenset(
    {
        "common",
        "events",
        "flags",
        "fonts",
        "gfx",
        "interface",
        "map",
        "music",
        "prescripted_countries",
        "sound",
    }
)

# Text inside quotes and after `#` can hold braces that don't count. Neither
# runs past the end of a line, so removing them keeps line numbers right.
_NOISE = re.compile(r'"[^"\n]*"|#[^\n]*')
_BRACE = re.compile(r"[{}]")


def check_script(name: str, data: bytes) -> list[Issue]:
    """Unbalanced braces.

    A `}` with nothing to close is an error. A `{` left open at the end is a
    warning: the game closes it at the end of the file, and two of its own
    files do this without any error in the log.
    """
    text = data.decode("utf-8", "replace")
    if '"' in text or "#" in text:
        text = _NOISE.sub("", text)
    opened: list[int] = []
    for match in _BRACE.finditer(text):
        if match.group() == "{":
            opened.append(match.start())
        elif opened:
            opened.pop()
        else:
            return [
                Issue(
                    "error",
                    'This "}" has no "{" to close, so the game stops reading the file '
                    "here and ignores the rest of it.",
                    name,
                    _line_at(text, match.start()),
                )
            ]
    if opened:
        return [
            Issue(
                "warning",
                'The "{" on this line is never closed. The game closes it at the end of '
                'the file, which is often harmless, but a "}" may be missing inside it.',
                name,
                _line_at(text, opened[0]),
            )
        ]
    return []


def _line_at(text: str, pos: int) -> int:
    return text.count("\n", 0, pos) + 1


# Descriptors

# What the game accepts. It logs "Invalid supported_version" for "3.*" and
# "v4.*.*", but takes "2.1.*" and "v4.5.1".
_SUPPORTED = re.compile(r"v?\d+\.\d+\.(\d+|\*)")


def check_descriptor(mod: Mod, game_version: str) -> list[Issue]:
    where = Path(mod.descriptor_file).name if mod.descriptor_file else "descriptor.mod"
    if mod.problem:
        # Nothing else in it can be read.
        return [Issue("error", f"{mod.problem}. The game may not load this mod.", where)]
    issues: list[Issue] = []
    if not mod.named:
        issues.append(
            Issue("error", "The descriptor has no name, so the launcher can't list it.", where)
        )
    if mod.source == "local":
        if not mod.root and not mod.archive:
            issues.append(
                Issue(
                    "error",
                    "The .mod file doesn't say where the mod is: it has no path or archive "
                    "line. The game can't load it.",
                    where,
                )
            )
        elif (mod.archive and not Path(mod.archive).is_file()) or (
            not mod.archive and not Path(mod.root).is_dir()
        ):
            issues.append(
                Issue(
                    "error",
                    f"The .mod file points at {mod.archive or mod.root}, which doesn't exist. "
                    "The game can't load it.",
                    where,
                )
            )

    supported = mod.supported_version
    example = _example_version(game_version)
    if not supported:
        issues.append(
            Issue(
                "warning",
                "No supported_version, so the launcher can't tell which game version this is for.",
                where,
            )
        )
    elif not _SUPPORTED.fullmatch(supported):
        issues.append(
            Issue(
                "warning",
                f'The game can\'t read supported_version "{supported}" and logs it as '
                f'invalid. It should look like "{example}".',
                where,
            )
        )
    if supported and is_outdated(supported, game_version):
        issues.append(
            Issue(
                "warning",
                f"Made for {supported}, but the game is {game_version}, so the launcher "
                "marks it outdated. It may still work.",
                where,
            )
        )
    return issues


def _example_version(game_version: str) -> str:
    parts = game_version.removeprefix("v").split(".")
    return f"v{parts[0]}.{parts[1]}.*" if len(parts) >= 2 else "v4.5.*"


# Every file in a mod


def check_files(mod: Mod, stamps: Mapping[str, Stamp], ctx: JobContext | None = None) -> Health:
    """Read a mod's localisation and script files and check each one."""
    issues: list[Issue] = []
    with ModFiles(mod, stamps) as files:
        for name in files.names():
            folded = name.casefold()
            if folded.endswith(".yml") and folded.startswith(LOC_FOLDERS):
                check = check_localisation
            elif (
                folded.endswith(SCRIPT_SUFFIXES)
                and folded.partition("/")[0] in SCRIPT_FOLDERS
                and "/" in folded
            ):
                check = check_script
            else:
                continue
            if ctx:
                ctx.check_cancelled()
            data = files.read(name)
            if data is not None:
                issues += check(name, data)
    return tuple(issues)


# The job


class CachedHealth(msgspec.Struct, frozen=True):
    fingerprint: int  # changes when any of the mod's files does
    issues: Health


class HealthCache(msgspec.Struct):
    version: int = CHECKS_VERSION
    mods: dict[str, CachedHealth] = msgspec.field(default_factory=dict)


@dataclass
class HealthChecker:
    """A health check of every installed mod. Call it with a JobContext to run it.

    Checking all 55 mods on a real install from scratch reads about 200 MB and
    takes a few seconds. After that only changed mods are read again.
    """

    library: Library
    cache_file: Path

    def __call__(self, ctx: JobContext) -> dict[str, Health]:
        cache = load_msgpack(self.cache_file, HealthCache)
        if cache is None or cache.version != CHECKS_VERSION:
            cache = HealthCache()

        mods = [m for m in self.library.mods if m.installed]
        game_version = self.library.game.version
        results: dict[str, Health] = {}
        kept: dict[str, CachedHealth] = {}
        for done, mod in enumerate(mods):
            ctx.progress(done, len(mods), "Checking mods")
            stamps = self.library.files.get(mod.key, {})
            fingerprint = _fingerprint(mod, stamps)
            entry = cache.mods.get(mod.key)
            if entry is None or entry.fingerprint != fingerprint:
                entry = CachedHealth(fingerprint, check_files(mod, stamps, ctx))
            kept[mod.key] = entry
            results[mod.key] = (*check_descriptor(mod, game_version), *entry.issues)

        new_cache = HealthCache(mods=kept)
        if new_cache != cache:
            save_msgpack(self.cache_file, new_cache)
        return results


def _fingerprint(mod: Mod, stamps: Mapping[str, Stamp]) -> int:
    data = msgspec.msgpack.encode([mod.root, mod.archive, sorted(stamps.items())])
    return xxhash.xxh3_64_intdigest(data)
