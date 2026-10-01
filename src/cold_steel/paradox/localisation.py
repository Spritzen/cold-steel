"""Localisation files: `localisation/**/*_l_<language>.yml`.

    l_english:
     tech_lasers_1:0 "Red Lasers"

Each file says its language on its first line. `keys()` gives each `KEY: "text"`
line as a script `Entry`, so conflict checks treat both kinds of file alike.
"""

import re
from pathlib import Path

from cold_steel.paradox.script import Entry

_LANGUAGE = re.compile(rb"\A(?:\xef\xbb\xbf)?(?:[ \t]*+(?:#[^\n]*+)?\r?\n)*+[ \t]*(l_\w+)[ \t]*:")
# The `:0` is optional (decision 29).
_KEY = re.compile(rb'^[ \t]*([^\s:#"]+)[ \t]*:[ \t]*\d*[ \t]*(".*)$', re.MULTILINE)


def language(data: bytes) -> str:
    """The language the first line names, like "l_english", or "" if there's none."""
    found = _LANGUAGE.match(data)
    return found.group(1).decode("ascii", "replace") if found else ""


def keys(data: bytes) -> list[Entry]:
    entries: list[Entry] = []
    line = 1
    counted = 0
    for found in _KEY.finditer(data):
        start = found.start(1)
        line += data.count(b"\n", counted, start)
        counted = start
        end = found.end(2)
        if data[end - 1 : end] == b"\r":
            end -= 1
        entries.append(Entry(found.group(1), start, found.start(2), end, line, False))
    return entries


DEFAULT_LANGUAGE = "l_english"
_SETTING = re.compile(rb'^\s*language\s*=\s*"?(l_\w+)"?', re.MULTILINE)


def game_language(data_dir: Path) -> str:
    """The language the game is set to, from its settings.txt. English if unknown."""
    try:
        found = _SETTING.search((data_dir / "settings.txt").read_bytes())
    except OSError:
        return DEFAULT_LANGUAGE
    return found.group(1).decode("ascii") if found else DEFAULT_LANGUAGE
