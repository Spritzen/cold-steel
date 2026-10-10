"""The empire checker: every list at once, against the game and the mods each
playset has, with each problem explained (Phase 10).

    report = check_lists(lists, users, catalog, installed, game.version, game_format)
    for found in report.lists:
        found.empires  # the marked ones, each with its problems in words
    lists.set_sources(owner, found.sources)  # on the main thread: what the check recorded

An empire's sources are the mods that supplied its modded things, with their
versions, recorded when it entered its list. They let the checker tell "this
mod was removed" from "this mod was updated and dropped the thing". An empire
already in a list when Phase 10 arrived gets them from the first check, marked
found later (decision 116).
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime

from cold_steel.core.definitions import digest
from cold_steel.core.empire_conversions import Conversion, conversion_steps
from cold_steel.core.empires import KINDS, Catalog, EmpireLists, Need, empire_needs
from cold_steel.core.index import Index
from cold_steel.core.mods import Mod, base_key
from cold_steel.paradox.empires import Empire, EmpireInfo, Use
from cold_steel.store.empires import LOOSE, Source, Sources, UseSources

# What kind of problem an empire has.
GONE = "gone"  # it uses something neither the game nor any installed mod defines
LACKING = "lacking"  # only mods a playset using its list lacks define it
BROKEN = "broken"  # its block can't be read


@dataclass(frozen=True)
class PlaysetMods:
    """A playset that plays a list: the playset itself, or a build of it."""

    name: str
    mods: frozenset[str]  # playset_mods(), by base key


@dataclass(frozen=True)
class Problem:
    kind: str  # GONE, LACKING or BROKEN
    text: str  # in words, for the report


@dataclass(frozen=True)
class EmpireReport:
    name: str
    problems: tuple[Problem, ...]


@dataclass(frozen=True)
class ListReport:
    owner: str  # a playset id, or LOOSE
    count: int  # its empires
    empires: tuple[EmpireReport, ...] = ()  # the marked ones, in list order
    # Why Play won't give the game this list: its format, or it can't be read. "" if none.
    problem: str = ""
    played: str = ""  # it was last played by an older game than this one. Not a mark
    convertible: bool = False  # it's in an older format that Convert… can convert
    # What the check recorded, to keep in lists.json: by empire name, None to forget one.
    sources: dict[str, Sources | None] = field(default_factory=dict)

    @property
    def marked(self) -> int:
        return self.count if self.problem else len(self.empires)


@dataclass(frozen=True)
class CheckReport:
    lists: tuple[ListReport, ...] = ()

    def get(self, owner: str) -> ListReport | None:
        return next((r for r in self.lists if r.owner == owner), None)

    @property
    def marked(self) -> int:
        return sum(r.marked for r in self.lists)


# Sources


@dataclass(frozen=True)
class ModInfo:
    """An installed mod, as its sources name it."""

    name: str
    version: str  # "" when its descriptor has none
    updated: str = ""  # the date of its newest file: "2026-09-22". "" if not known


def installed_mods(mods: Sequence[Mod], index: Index | None = None) -> dict[str, ModInfo]:
    """Each installed mod by key, and by base key for a pinned copy whose
    Workshop mod isn't installed. Pass Library.every_mod, and the object index
    for the date of each mod's newest file."""
    found: dict[str, ModInfo] = {}
    for mod in mods:
        source = index.sources.get(mod.key) if index else None
        newest = max((t for _, t in source.stamps.values()), default=0) if source else 0
        updated = datetime.fromtimestamp(newest / 1e9).date().isoformat() if newest else ""
        found[mod.key] = ModInfo(mod.name, mod.version, updated)
    for mod in mods:
        found.setdefault(base_key(mod.key), found[mod.key])
    return found


def make_sources(
    info: EmpireInfo,
    block: bytes,
    catalog: Catalog,
    installed: Mapping[str, ModInfo],
    *,
    later: bool = False,
) -> Sources:
    """The mods that define what an empire uses and the game doesn't, now."""
    uses = tuple(
        UseSources(need.use[0], need.use[1], _sources(need, installed))
        for need in empire_needs(info, catalog)
    )
    return Sources(digest(block), uses, later=later)


def _sources(need: Need, installed: Mapping[str, ModInfo]) -> tuple[Source, ...]:
    found: dict[tuple[str, str, str], Source] = {}
    for key in need.mods:
        mod = installed.get(key)
        if mod is not None:
            found.setdefault(
                (base_key(key), mod.version, mod.updated),
                Source(base_key(key), mod.name, mod.version, mod.updated),
            )
    return tuple(found.values())


def pending(block: bytes) -> Sources:
    """For an empire that entered a list before its mods were known."""
    return Sources(digest(block), pending=True)


def sources_for(
    empires: Sequence[Empire],
    catalog: Catalog | None,
    installed: Mapping[str, ModInfo],
    known: Mapping[str, Sources] | None = None,
) -> dict[str, Sources | None]:
    """Sources for empires entering a list, by name: those `known` from the
    list they came from when it's the same block, else the mods that define
    their things now, else pending until the next check."""
    found: dict[str, Sources | None] = {}
    for empire in empires:
        held = known.get(empire.name) if known else None
        if held is not None and held.digest == digest(empire.text):
            found[empire.name] = held
        elif empire.info is None:
            found[empire.name] = None
        elif catalog is None:
            found[empire.name] = pending(empire.text)
        else:
            found[empire.name] = make_sources(empire.info, empire.text, catalog, installed)
    return found


# Checking every list


def check_lists(
    lists: EmpireLists,
    users: Mapping[str, Sequence[PlaysetMods]],
    catalog: Catalog,
    installed: Mapping[str, ModInfo],
    game: str,
    game_format: str,
    conversions: Sequence[Conversion] = (),
) -> CheckReport:
    """Check every list: each playset's, and the loose one. `users` gives, by
    owner, the playsets that play each list. `game` is the game's version and
    `game_format` the empire file format it uses now."""
    owners = [*lists.owners(), LOOSE]
    return CheckReport(
        tuple(
            check_list(
                lists,
                owner,
                users.get(owner, ()),
                catalog,
                installed,
                game,
                game_format,
                conversions,
            )
            for owner in owners
        )
    )


def check_list(
    lists: EmpireLists,
    owner: str,
    users: Sequence[PlaysetMods],
    catalog: Catalog,
    installed: Mapping[str, ModInfo],
    game: str,
    game_format: str,
    conversions: Sequence[Conversion] = (),
) -> ListReport:
    try:
        file = lists.read(owner)
    except OSError as error:
        return ListReport(owner, 0, problem=f"Its list couldn't be read: {error}")
    info = lists.info(owner)
    names = set(file.names)
    sources: dict[str, Sources | None] = {n: None for n in info.sources if n not in names}
    marked: list[EmpireReport] = []
    for empire in file.empires:
        if empire.info is None:
            marked.append(EmpireReport(empire.name, (Problem(BROKEN, empire.problem),)))
            continue
        held = info.sources.get(empire.name)
        block = digest(empire.text)
        if held is None or held.pending or held.digest != block:
            later = held is None or held.digest != block
            held = make_sources(empire.info, empire.text, catalog, installed, later=later)
            sources[empire.name] = held
        problems = _problems(empire_needs(empire.info, catalog), held, users, installed)
        if problems:
            marked.append(EmpireReport(empire.name, problems))

    problem, convertible = "", False
    if file.empires and info.format and game_format and info.format != game_format:
        convertible = conversion_steps(conversions, info.format, game_format) is not None
        problem = format_text(info.format, game_format, convertible=convertible)
    played = ""
    if file.empires and older_game(info.played, game):
        played = (
            f"It was last played by the game {info.played}, and the game is {game} now. "
            "The game checks each empire when you open it in its empire creator."
        )
    return ListReport(
        owner, len(file.empires), tuple(marked), problem, played, convertible, sources
    )


def format_text(have: str, now: str, *, convertible: bool) -> str:
    said = f"Its empires are in empire file {have}'s format, and the game uses {now}'s."
    if convertible:
        return f"{said} Convert… in its Empires window can convert them."
    return f"{said} No conversion is known: rebuild them in the game's empire creator."


def older_game(played: str, game: str) -> bool:
    """`played` is an older game than `game`, by major and minor version."""
    have, now = _major_minor(played), _major_minor(game)
    return bool(have and now and have < now)


def _major_minor(version: str) -> tuple[int, ...]:
    parts = version.lstrip("v").split(".")[:2]
    return tuple(int(p) for p in parts) if all(p.isdigit() for p in parts) else ()


def _problems(
    needs: Sequence[Need],
    held: Sources | None,
    users: Sequence[PlaysetMods],
    installed: Mapping[str, ModInfo],
) -> tuple[Problem, ...]:
    problems: list[Problem] = []
    for need in needs:
        if not need.mods:
            problems.append(Problem(GONE, gone_text(need.use, held, installed)))
            continue
        lacking = [u.name for u in users if not any(base_key(m) in u.mods for m in need.mods)]
        if lacking:
            away, changed = from_sources(need.use, held, installed, need.mods)
            said = [lacking_text(need, lacking, installed), *changed, away]
            problems.append(Problem(LACKING, " ".join(t for t in said if t)))
    return tuple(problems)


def gone_text(use: Use, held: Sources | None, installed: Mapping[str, ModInfo]) -> str:
    """Why something an empire uses is in no installed mod, from its sources
    if they're known: "Made with Planetary Diversity 3.2. Its planet class
    pc_pd_cold_superhabitable is gone in 3.4.". A mod without a version is
    named by the date of its newest file instead."""
    kind, key = KINDS.get(use[0], use[0]), use[1]
    away, changed = from_sources(use, held, installed, ())
    said = changed or [f"Its {kind} {key} isn't in the game or any installed mod."]
    return " ".join([*said, away] if away else said)


def from_sources(
    use: Use, held: Sources | None, installed: Mapping[str, ModInfo], defined_by: Sequence[str]
) -> tuple[str, list[str]]:
    """What an empire's sources say about a thing it uses, for each mod that
    had it then but isn't among `defined_by` (those that have it now): one
    sentence for the mods no longer installed, and one for each that's
    installed but dropped it."""
    kind, key = KINDS.get(use[0], use[0]), use[1]
    found = next((u for u in held.uses if (u.folder, u.key) == use), None) if held else None
    if held is None or found is None:
        return "", []
    has_it = {base_key(m) for m in defined_by}
    made = "Found later with" if held.later else "Made with"
    gone: list[str] = []
    changed: list[str] = []
    for source in found.mods:
        if source.mod in has_it:
            continue
        now = installed.get(source.mod)
        had = _named(source.name, source.version, source.updated)
        if now is None:
            gone.append(had)
        elif now.version and now.version != source.version:
            changed.append(f"{made} {had}. Its {kind} {key} is gone in {now.version}.")
        elif not now.version and now.updated and now.updated != source.updated:
            changed.append(
                f"{made} {had}. Its {kind} {key} is gone in its update of {now.updated}."
            )
        else:
            changed.append(f"{made} {had}. Its {kind} {key} is gone from it now.")
    if not gone:
        return "", changed
    verb = "isn't" if len(gone) == 1 else "aren't"
    return f"{made} {' and '.join(gone)}, which {verb} installed now.", changed


def lacking_text(need: Need, lacking: Sequence[str], installed: Mapping[str, ModInfo]) -> str:
    kind, key = KINDS.get(need.use[0], need.use[0]), need.use[1]
    mods = " or ".join(dict.fromkeys(_mod_name(m, installed) for m in need.mods))
    who = " and ".join(lacking)
    verb = "lacks" if len(lacking) == 1 else "lack"
    return f"Its {kind} {key} is only in {mods}, which {who} {verb}."


def _mod_name(key: str, installed: Mapping[str, ModInfo]) -> str:
    mod = installed.get(key)
    return mod.name if mod else key


def _named(name: str, version: str, updated: str) -> str:
    """ "Planetary Diversity 3.2", or without a version "Planetary Diversity,
    updated 2026-09-22"."""
    if version:
        return f"{name} {version}"
    return f"{name}, updated {updated}" if updated else name
