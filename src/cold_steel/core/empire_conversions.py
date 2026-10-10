"""Converting a list to a new empire file format, only for changes we've seen
(decision 115).

The table is data, in `cold_steel/data/empire_conversions.json`. Each row takes
lists from one format to the next, and names what the game renamed between
them. A row with no renames says the blocks mean the same in both:

    {"from": "3.4", "to": "3.5", "renames": [
        {"field": "planet_class", "from": "pc_old", "to": "pc_new"},   a value
        {"field": "old_field", "to_field": "new_field"}                a field
    ]}

Converting changes the empires' text, which Cold Steel otherwise never does.
So it runs only from Convert…, which shows every change first and keeps a
version of the list.
"""

import re
from collections import deque
from collections.abc import Sequence
from dataclasses import dataclass
from importlib import resources

import msgspec

from cold_steel.core.empires import EmpireLists
from cold_steel.paradox.empires import EmpireFile, join_empires


class Rename(msgspec.Struct, frozen=True):
    field: str  # the field, at any depth: "planet_class", "trait"
    old: str = msgspec.field(default="", name="from")  # a value to rename
    new: str = msgspec.field(default="", name="to")
    to_field: str = ""  # or the field's new name


class Conversion(msgspec.Struct, frozen=True):
    old: str = msgspec.field(name="from")  # "3.4"
    new: str = msgspec.field(name="to")  # "3.5"
    renames: tuple[Rename, ...] = ()


class _Table(msgspec.Struct):
    conversions: tuple[Conversion, ...] = ()


def load_conversions(data: bytes | None = None) -> tuple[Conversion, ...]:
    """The table: `data`, or the one that ships with Cold Steel."""
    if data is None:
        data = resources.files("cold_steel").joinpath("data/empire_conversions.json").read_bytes()
    return msgspec.json.decode(data, type=_Table).conversions


def conversion_steps(
    table: Sequence[Conversion], have: str, want: str
) -> tuple[Conversion, ...] | None:
    """The rows that take format `have` to `want`, in order. None if none do."""
    if have == want:
        return ()
    paths: deque[tuple[str, tuple[Conversion, ...]]] = deque([(have, ())])
    seen = {have}
    while paths:
        at, steps = paths.popleft()
        for row in table:
            if row.old == at and row.new not in seen:
                if row.new == want:
                    return (*steps, row)
                seen.add(row.new)
                paths.append((row.new, (*steps, row)))
    return None


def convert_block(text: bytes, steps: Sequence[Conversion]) -> bytes:
    """One empire's block with every rename made. Spacing and everything else stay."""
    out = text.decode("utf-8", "surrogateescape")
    for step in steps:
        for rename in step.renames:
            out = _rename(out, rename)
    return out.encode("utf-8", "surrogateescape")


def _rename(text: str, rename: Rename) -> str:
    name = re.escape(rename.field)
    if rename.to_field:
        return re.sub(rf"(?<![\w.]){name}(?=\s*=)", rename.to_field, text)
    if not rename.old:
        return text
    old = re.escape(rename.old)
    pattern = rf'(?<![\w.])({name}\s*=\s*)("?){old}\2(?![\w.])'
    return re.sub(pattern, lambda m: f"{m[1]}{m[2]}{rename.new}{m[2]}", text)


@dataclass(frozen=True)
class Converted:
    """One empire's changes: each line before and after."""

    name: str
    lines: tuple[tuple[str, str], ...]


@dataclass(frozen=True)
class ConversionPlan:
    format: str  # the format it converts to
    data: bytes  # the converted list
    changed: tuple[Converted, ...]  # the empires whose text changes


def plan_conversion(
    file: EmpireFile, have: str, want: str, table: Sequence[Conversion]
) -> ConversionPlan | None:
    """A list converted from format `have` to `want`. None if no rows do it."""
    steps = conversion_steps(table, have, want)
    if steps is None:
        return None
    texts: list[bytes] = []
    changed: list[Converted] = []
    for empire in file.empires:
        new = convert_block(empire.text, steps)
        texts.append(new)
        if new != empire.text:
            before = empire.text.decode("utf-8", "replace").splitlines()
            after = new.decode("utf-8", "replace").splitlines()
            lines = tuple(
                (a.strip(), b.strip()) for a, b in zip(before, after, strict=False) if a != b
            )
            changed.append(Converted(empire.name, lines))
    return ConversionPlan(want, join_empires(file.head, texts), tuple(changed))


def convert_list(lists: EmpireLists, owner: str, plan: ConversionPlan) -> None:
    """Write a converted list, after keeping a version of it, and record its
    new format. Raises OSError."""
    lists.write(owner, plan.data, f"Before converting to empire file {plan.format}")
    lists.set_info(owner, format=plan.format)
