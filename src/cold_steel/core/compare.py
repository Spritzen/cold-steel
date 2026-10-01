"""Two versions of a file or object, side by side, with the lines that differ.

    pair = compare(index, left_claim, right_claim)
    pair.left.text, pair.left.changed  -> the text, and its changed line numbers

Reading runs off the main thread: a file may be big, or inside a zip.
"""

import difflib
from dataclasses import dataclass

from cold_steel.core.conflicts import Claim
from cold_steel.core.index import Index

# Files shown as text. Anything else is described, not shown.
TEXT_SUFFIXES = (
    ".txt",
    ".gui",
    ".gfx",
    ".asset",
    ".yml",
    ".csv",
    ".shader",
    ".fxh",
    ".lua",
    ".json",
    ".settings",
)
# A bigger file is cut short: no one reads 2 MB side by side.
MAX_BYTES = 2_000_000


@dataclass(frozen=True)
class Version:
    text: str
    first_line: int  # the file line the text starts at
    changed: frozenset[int]  # lines of `text` that differ from the other side, from 0


@dataclass(frozen=True)
class Pair:
    left: Version
    right: Version
    same: bool  # nothing differs


def version_text(index: Index, claim: Claim) -> tuple[str, int]:
    """A version's text and the line it starts at."""
    data = index.read(claim.layer, claim.path)
    if data is None:
        return "(This file can't be read any more. Rescan to see the current files.)", 1
    if claim.definition is not None:
        d = claim.definition
        return decode(data[d.start : d.end]), d.line
    if not claim.path.lower().endswith(TEXT_SUFFIXES):
        return f"(Not a text file: {len(data):,} bytes.)", 1
    text = decode(data[:MAX_BYTES])
    if len(data) > MAX_BYTES:
        text += f"\n\n(Cut short: the file is {len(data):,} bytes.)"
    return text, 1


def decode(data: bytes) -> str:
    """Paradox files are UTF-8, but older mods are often Windows-1252."""
    data = data.removeprefix(b"\xef\xbb\xbf")
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return data.decode("cp1252", "replace")


def compare(index: Index, left: Claim, right: Claim) -> Pair:
    left_text, left_line = version_text(index, left)
    right_text, right_line = version_text(index, right)
    a, b = left_text.splitlines(), right_text.splitlines()
    changed_a: set[int] = set()
    changed_b: set[int] = set()
    # Compare ignoring indentation and trailing spaces; tabs versus spaces isn't a change.
    matcher = difflib.SequenceMatcher(None, [s.strip() for s in a], [s.strip() for s in b])
    for tag, a1, a2, b1, b2 in matcher.get_opcodes():
        if tag != "equal":
            changed_a.update(range(a1, a2))
            changed_b.update(range(b1, b2))
    return Pair(
        Version(left_text, left_line, frozenset(changed_a)),
        Version(right_text, right_line, frozenset(changed_b)),
        not changed_a and not changed_b,
    )
