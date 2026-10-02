"""Where a playset's mods clash, and which one the game uses.

    found = ConflictFinder(index, playset, library)(ctx)
    for conflict in found.conflicts: ...

Two kinds of clash:

- **File:** two mods ship a file at the same path. The game reads only the one
  from the mod loaded last.
- **Object:** two mods define the same object (the same technology, event,
  localisation key...) in different files. Who wins depends on the folder's
  rule (merge_rules.py): usually the file whose name sorts last, but in some
  folders the first.

The game's own files take part, as the first layer, so a winner can be the
game itself. A clash only counts as one when two or more mods are involved;
a mod changing the game's object is what mods are for.
"""

from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import NamedTuple

from cold_steel.core.definitions import Definition
from cold_steel.core.index import GAME, Index, ObjectKey
from cold_steel.core.jobs import JobContext
from cold_steel.core.library import Library
from cold_steel.core.merge_rules import Rule, Rules, load_rules
from cold_steel.store.playsets import Playset

FILE = "file"  # the kind of a file conflict


class Claim(NamedTuple):
    """One layer's version of a file or object."""

    layer: str  # GAME or a Mod.key
    path: str  # the file, as written in that layer
    definition: Definition | None  # None for a whole file


# Makes a Claim without NamedTuple's Python-level constructor: a playset can
# have over half a million of them, and this is several times faster.
_new_claim = tuple.__new__


@dataclass(frozen=True)
class Conflict:
    kind: str  # FILE, or the kind of object, like "common/technology"
    key: str  # the file's path, or the object's name
    claims: tuple[Claim, ...]  # in the order the game reads them
    winner: int  # which claim the game uses
    reason: str  # why that one, in plain words
    identical: bool  # every mod's version means the same thing
    rule: Rule | None = None  # the folder's rule, for an object

    @property
    def mods(self) -> tuple[str, ...]:
        return tuple(dict.fromkeys(c.layer for c in self.claims if c.layer != GAME))

    @property
    def winning(self) -> Claim:
        return self.claims[self.winner]


@dataclass(frozen=True)
class Found:
    """Everything the conflict window shows for one playset."""

    order: tuple[str, ...]  # GAME, then each enabled mod in load order
    conflicts: tuple[Conflict, ...]
    index: Index = field(repr=False)
    # Files another mod's file replaced. The game never reads their objects.
    replaced: frozenset[tuple[str, str]] = field(default=frozenset(), repr=False)

    def claims(self, kind: str, key: str) -> tuple[Claim, ...]:
        """Where an object is defined in this playset, in load order."""
        if kind == FILE:
            folded = key.lower()
            return tuple(
                _new_claim(Claim, (layer, paths[folded], None))
                for layer in self.order
                if folded in (paths := self.index.tables(layer).paths)
            )
        return tuple(
            _new_claim(Claim, (layer, path, definition))
            for layer in self.order
            for path, definition in self.index.tables(layer).objects.get((kind, key), ())
            if (layer, path) not in self.replaced
        )

    def search(self, text: str, limit: int) -> list[tuple[str, str, tuple[Claim, ...]]]:
        """Objects and files whose name holds `text`, ignoring case: (kind, key, claims).

        Looks through every object in the playset, so run it off the main thread.
        """
        text = text.casefold()
        seen: set[tuple[str, str]] = set()
        for layer in self.order:
            tables = self.index.tables(layer)
            names = [(FILE, folded) for folded in tables.paths if text in folded]
            names += [(kind, key) for kind, key in tables.objects if text in key.casefold()]
            for name in names:
                if len(seen) >= limit:
                    break
                seen.add(name)
        found = [(kind, key, self.claims(kind, key)) for kind, key in seen]
        found = [r for r in found if r[2]]
        return [
            (kind, claims[-1].path if kind == FILE else key, claims)
            for kind, key, claims in sorted(found, key=lambda r: (r[0], r[1].casefold()))
        ]


@dataclass
class ConflictFinder:
    index: Index
    playset: Playset
    library: Library
    rules: Rules = field(default_factory=load_rules)
    # Mods to leave out: the playset's own patch mod, so the clashes it settles still show.
    leave_out: frozenset[str] = frozenset()

    def __call__(self, ctx: JobContext) -> Found:
        installed = {m.key for m in self.library.every_mod if m.installed}
        enabled = (
            e.key
            for e in self.playset.entries
            if e.enabled and e.key in installed and e.key not in self.leave_out
        )
        order = tuple(k for k in (GAME, *enabled) if k in self.index.layers)
        ctx.progress(0, 0, "Finding conflicts")
        tables = [self.index.tables(layer) for layer in order]
        ctx.check_cancelled()

        # Counting runs in C. Only names found in two layers or more are looked at.
        paths: Counter[str] = Counter()
        objects: Counter[ObjectKey] = Counter()
        for table in tables:
            paths.update(table.paths.keys())  # keys: a dict would add its values
            objects.update(table.objects.keys())
        ctx.check_cancelled()

        found: list[Conflict] = []
        replaced: set[tuple[str, str]] = set()
        for folded, count in paths.items():
            if count > 1:
                claims = tuple(
                    _new_claim(Claim, (layer, table.paths[folded], None))
                    for layer, table in zip(order, tables, strict=True)
                    if folded in table.paths
                )
                replaced.update((c.layer, c.path) for c in claims[:-1])
                conflict = _file_conflict(claims, self.index)
                if conflict is not None:
                    found.append(conflict)

        # Names in two layers or more, gathered from just the layers that have them.
        shared = {key for key, count in objects.items() if count > 1}
        gathered: dict[ObjectKey, list[Claim]] = {}
        for layer, table in zip(order, tables, strict=True):
            for name in shared & table.objects.keys():
                versions = gathered.setdefault(name, [])
                for path, definition in table.objects[name]:
                    if (layer, path) not in replaced:
                        versions.append(_new_claim(Claim, (layer, path, definition)))
        ctx.check_cancelled()
        position = {layer: n for n, layer in enumerate(order)}
        for (kind, key), versions in gathered.items():
            if len({c.layer for c in versions}) > 1:
                conflict = self._object_conflict(kind, key, tuple(versions), position)
                if conflict is not None:
                    found.append(conflict)

        found.sort(key=lambda c: (c.kind, c.key.casefold()))
        return Found(order, tuple(found), self.index, frozenset(replaced))

    def _object_conflict(
        self, kind: str, key: str, claims: tuple[Claim, ...], position: dict[str, int]
    ) -> Conflict | None:
        rule = self.rules.for_file(claims[0].path)
        if rule.winner == "merged":
            return None
        ordered, winner, reason = rank(claims, rule, position)
        digests = {c.definition.digest for c in ordered if c.definition and c.layer != GAME}
        return Conflict(kind, key, ordered, winner, reason, len(digests) <= 1, rule)


def rank(
    claims: Iterable[Claim], rule: Rule, position: dict[str, int]
) -> tuple[tuple[Claim, ...], int, str]:
    """An object's claims in the order the game reads them, which one it uses,
    and why. `position` is each layer's place in the load order."""
    ordered = tuple(sorted(claims, key=lambda c: _sort_key(c, position)))
    winner, reason = _winner(ordered, rule, position)
    return ordered, winner, reason


def _file_conflict(claims: tuple[Claim, ...], index: Index) -> Conflict | None:
    mods = [c for c in claims if c.layer != GAME]
    if not mods:
        return None
    hashes = {index.layers[c.layer].files[c.path].hash for c in mods}
    sizes = {index.layers[c.layer].files[c.path].stamp[0] for c in mods}
    # Files that aren't read (pictures, sounds) have no hash, so they're never
    # called identical: equal sizes alone prove nothing.
    identical = len(sizes) == 1 and 0 not in hashes and len(hashes) == 1
    return Conflict(
        FILE,
        claims[-1].path,
        claims,
        len(claims) - 1,
        "Loaded last, so its file replaces the others at this path.",
        identical,
    )


def _sort_key(claim: Claim, position: dict[str, int]) -> tuple[str, int, int]:
    """The order the game reads definitions in: by file name, then load order."""
    name = claim.path.rpartition("/")[2]
    start = claim.definition.start if claim.definition else 0
    return name, position[claim.layer], start


def _winner(ordered: tuple[Claim, ...], rule: Rule, position: dict[str, int]) -> tuple[int, str]:
    folder = ordered[0].path.rpartition("/")[0]
    if rule.winner == "localisation":
        return _localisation_winner(ordered)
    if rule.first_wins:
        winner = 0
        rival = _next_rival(ordered, winner, range(1, len(ordered)))
        how = "first"
    else:
        winner = len(ordered) - 1
        rival = _next_rival(ordered, winner, range(winner - 1, -1, -1))
        how = "last"
    win = ordered[winner]
    name = win.path.rpartition("/")[2]
    if rival is not None and rival.path.rpartition("/")[2] == name:
        return winner, (
            f"Both files are named {name}, so load order decides: "
            f"{'the earlier' if how == 'first' else 'the later'} mod's definition wins."
        )
    return winner, (
        f"In {folder}/ the game keeps the {how} definition it reads, and it reads "
        f"files in name order. {name} sorts {how}."
    )


def _next_rival(ordered: tuple[Claim, ...], winner: int, others: Iterable[int]) -> Claim | None:
    """The closest claim from a different layer, the one the winner beat."""
    for n in others:
        if ordered[n].layer != ordered[winner].layer:
            return ordered[n]
    return None


def _localisation_winner(ordered: tuple[Claim, ...]) -> tuple[int, str]:
    """A key in a replace/ folder beats the rest; otherwise the first file name wins.

    Both checked in game on 2026-10-01. Which replace/ file wins when several
    have the key isn't checked yet: the last name is assumed, as they override.
    """
    replacing = [n for n, c in enumerate(ordered) if "/replace/" in c.path.lower()]
    if replacing:
        winner = replacing[-1]
        name = ordered[winner].path.rpartition("/")[2]
        why = "It's in a replace/ folder, which beats other localisation."
        if len(replacing) > 1:
            why += f" Of the replace/ files, {name} sorts last (not yet checked in game)."
        return winner, why
    name = ordered[0].path.rpartition("/")[2]
    return 0, (
        "Outside replace/ folders the game keeps the first definition it reads, "
        f"and it reads files in name order. {name} sorts first."
    )
