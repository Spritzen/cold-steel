"""Old copies: a mod made for an older game version replacing a newer mod's files.

    copies = find_old_copies(found, versions)

A compatibility patch often ships its own copy of another mod's file. When the
other mod is updated and the patch isn't, the patch's old copy still replaces
the new one, and whatever the new file added is lost.

Two things must both be true before we say so:

- The replacing mod is made for an older game version (major.minor) than the
  mod it replaces. Versions we can't read, or with a `*` there, don't count.
- Something the replaced file defines is missing from the game: no file the
  game reads defines it any more. An object that only moved to another file
  isn't missing.

Older alone is too common: authors often don't update `supported_version`.
On the real playsets, each test alone flagged mods that worked; together they
flagged only the one that broke.
"""

from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass

from cold_steel.core.conflicts import Found
from cold_steel.core.index import GAME, ObjectKey
from cold_steel.core.version import is_outdated

# How many missing objects a description names before "and N more".
NAMED = 3


@dataclass(frozen=True)
class OldCopy:
    """One mod replacing another mod's files with copies made for an older game."""

    mod: str  # the Mod.key with the old copies
    replaces: str  # the Mod.key of the newer mod
    mod_version: str  # their supported_version
    replaces_version: str
    files: tuple[str, ...]  # the replaced files, as the newer mod writes them
    missing: tuple[ObjectKey, ...]  # (kind, name): defined there, now nowhere


def find_old_copies(found: Found, versions: Mapping[str, str]) -> tuple[OldCopy, ...]:
    """Every old copy in the playset `found` was made for, the worst first.

    `versions` is each Mod.key's supported_version.
    """
    index = found.index
    tables = {layer: index.tables(layer) for layer in found.order}
    mods = [layer for layer in found.order if layer != GAME]

    shared: Counter[str] = Counter()
    for layer in mods:
        shared.update(tables[layer].paths.keys())
    if not any(count > 1 for count in shared.values()):
        return ()

    alive = {
        name
        for layer in found.order
        for name, places in tables[layer].objects.items()
        if any((layer, path) not in found.replaced for path, _ in places)
    }

    pairs: dict[tuple[str, str], tuple[list[str], dict[ObjectKey, None]]] = {}
    for folded, count in shared.items():
        if count < 2:
            continue
        owners = [layer for layer in mods if folded in tables[layer].paths]
        winner = owners[-1]
        new = versions.get(winner, "")
        won = index.layers[winner].files[tables[winner].paths[folded]]
        for loser in owners[:-1]:
            if not is_outdated(new, versions.get(loser, "")):
                continue
            path = tables[loser].paths[folded]
            lost = index.layers[loser].files[path]
            if won.hash and won.hash == lost.hash:
                continue
            files, missing = pairs.setdefault((winner, loser), ([], {}))
            files.append(path)
            for definition in lost.definitions:
                name = (definition.kind, definition.key)
                if name not in alive:
                    missing[name] = None

    copies = [
        OldCopy(
            mod,
            replaces,
            versions.get(mod, ""),
            versions.get(replaces, ""),
            tuple(sorted(files, key=str.casefold)),
            tuple(missing),
        )
        for (mod, replaces), (files, missing) in pairs.items()
        if missing
    ]
    copies.sort(key=lambda c: (-len(c.missing), c.mod, c.replaces))
    return tuple(copies)


def describe(copy: OldCopy, names: Mapping[str, str]) -> str:
    """One sentence for the user, naming a few of the missing objects."""
    mod, replaces = names.get(copy.mod, copy.mod), names.get(copy.replaces, copy.replaces)
    shown = ", ".join(key for _, key in copy.missing[:NAMED])
    more = len(copy.missing) - NAMED
    if more > 0:
        shown += f" and {more} more"
    return (
        f"{mod}, made for {copy.mod_version}, replaces {len(copy.files)} file(s) of "
        f"{replaces} ({copy.replaces_version}) with older copies. "
        f"{len(copy.missing)} thing(s) {replaces} adds are then missing from the game: {shown}."
    )
