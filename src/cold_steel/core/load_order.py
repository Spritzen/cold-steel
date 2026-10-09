"""Sort: a sensible default load order for a playset. A mod loaded later wins a
clash between whole files; objects follow each folder's rule (merge_rules.py).

1. Each mod loads after the mods it declares as dependencies.
2. Known "load first" and "load last" mods move to the top or bottom. The
   rules are in `cold_steel/data/load_order.json`.
3. Otherwise the user's order is kept.

Rule 1 beats rule 2: a "load first" mod that depends on another still loads
after it. If dependencies loop, the loop is broken at the mod the user put
first.
"""

import json
import re
from dataclasses import dataclass
from importlib import resources

import msgspec

from cold_steel.core.mods import Mod
from cold_steel.store.playsets import Playset

FIRST, MIDDLE, LAST = 0, 1, 2


@dataclass(frozen=True)
class Rules:
    first: tuple[re.Pattern[str], ...] = ()
    last: tuple[re.Pattern[str], ...] = ()

    def group(self, name: str) -> int:
        if any(p.search(name) for p in self.first):
            return FIRST
        if any(p.search(name) for p in self.last):
            return LAST
        return MIDDLE


def load_rules() -> Rules:
    text = resources.files("cold_steel").joinpath("data/load_order.json").read_text("utf-8")
    data = json.loads(text)

    def patterns(group: str) -> tuple[re.Pattern[str], ...]:
        return tuple(re.compile(r["pattern"], re.IGNORECASE) for r in data.get(group, []))

    return Rules(first=patterns("first"), last=patterns("last"))


def sort_playset(playset: Playset, mods: dict[str, Mod], rules: Rules | None = None) -> Playset:
    """The playset in its sorted order. `mods` is every known Mod by key."""
    rules = rules or load_rules()
    entries = list(playset.entries)

    def name(i: int) -> str:
        mod = mods.get(entries[i].key)
        return mod.name if mod else entries[i].name

    # Where each mod would go without dependencies: by group, then the user's order.
    rank = sorted(range(len(entries)), key=lambda i: (rules.group(name(i)), i))
    by_name: dict[str, list[int]] = {}
    for i in range(len(entries)):
        by_name.setdefault(name(i).casefold(), []).append(i)

    after: dict[int, set[int]] = {i: set() for i in range(len(entries))}
    for i, entry in enumerate(entries):
        mod = mods.get(entry.key)
        for dep in mod.dependencies if mod else ():
            after[i].update(j for j in by_name.get(dep.casefold(), []) if j != i)

    order: list[int] = []
    placed: set[int] = set()
    waiting = list(rank)
    while waiting:
        # The first mod (in rank order) whose dependencies are all placed.
        ready = next((i for i in waiting if after[i] <= placed), None)
        if ready is None:  # a loop: take the one the user put first
            ready = min(waiting)
        order.append(ready)
        placed.add(ready)
        waiting.remove(ready)
    return msgspec.structs.replace(playset, entries=tuple(entries[i] for i in order))
