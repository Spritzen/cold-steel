"""Reads the game's `logs/error.log`, written fresh on every run.

Each entry starts with the time and the place in the game's code that wrote it:

    [19:27:33][projectile_graphics_data.cpp:460]: An item with name "ion_cannon"
    already exists!  file: gfx/projectiles/apocalypse_weapons.txt line: 44

(one line in the file). Some entries run on over several lines; the extra lines
belong to the entry above them.

Most entries name the game file they're about, in one of a few shapes. Paths
are relative to the game folder, so the same path can be in the game and in
several mods.
"""

import re
from dataclasses import dataclass
from pathlib import Path

FILE_NAME = "logs/error.log"  # inside the Paradox user data folder

_ENTRY = re.compile(r"\[(\d\d:\d\d:\d\d)\]\[([^\]]*)\]: ?(.*)")

# In the order they're tried. The first match is the file the error is about.
_FILE_REFS = (
    # file: gfx/x.txt line: 44   /   in file: "sound/x.asset" near line: 16
    re.compile(r'file: *"?(?P<file>[^"\n]+?)"? +(?:near )?line: *(?P<line>\d+)'),
    # common/scripted_triggers/x.txt:64
    re.compile(r"(?<![\w/.])(?P<file>(?:[\w!.\-]+/)+[\w!.\-]+\.\w+):(?P<line>\d+)"),
    # Could not find files for mod: /home/.../workshop/content/281990/688086068
    re.compile(r"Could not find files for mod: (?P<file>/.+)"),
    # Any path: Couldn't find texture "gfx/models/x.dds"
    re.compile(r"(?<![\w/.])(?P<file>/?(?:[\w!.\-]+/)+[\w!.\-]+\.[A-Za-z0-9]{2,5})(?![\w/])"),
)


@dataclass(frozen=True, slots=True)
class LogEntry:
    time: str  # "19:27:33"
    source: str  # where in the game's code: "persistent.cpp:41"
    text: str
    file: str = ""  # the file it's about, as the game wrote it
    line: int = 0


def read_error_log(data_dir: Path) -> list[LogEntry]:
    """Raises OSError if there's no log."""
    data = (data_dir / FILE_NAME).read_bytes()
    return parse_error_log(data.decode("utf-8", errors="replace"))


def parse_error_log(text: str) -> list[LogEntry]:
    entries: list[tuple[str, str, list[str]]] = []
    for line in text.splitlines():
        match = _ENTRY.fullmatch(line)
        if match:
            entries.append((match[1], match[2], [match[3]]))
        elif entries and line.strip():
            entries[-1][2].append(line)
        # Anything before the first entry has no time or source; it's skipped.
    return [_entry(time, source, "\n".join(lines).strip()) for time, source, lines in entries]


def _entry(time: str, source: str, text: str) -> LogEntry:
    for pattern in _FILE_REFS:
        match = pattern.search(text)
        if match:
            line = match.groupdict().get("line")
            return LogEntry(time, source, text, match["file"].strip(), int(line or 0))
    return LogEntry(time, source, text)
