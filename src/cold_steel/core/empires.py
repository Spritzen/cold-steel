"""Empires: each playset's own list, which mods each empire needs, and
handing a list to the game while it runs.

    lists = EmpireLists(empires_dir(), backup_dir)
    put_in_game(lists, list_owner(playset), game_file, state_path, backup_dir)
    ... the game runs ...
    take_back(lists, state_path, backup_dir, exists)

Each playset has its own list of empires (decision 107), and a built playset
uses the list of the playset it was built from (decision 98). A list is kept
in the game's own format, each empire's block byte for byte as the game wrote
it. Play writes the playset's list as the game's empire file, and when the
game closes, the file is copied back into the list: whatever was made,
changed or deleted in game is kept. Empires found in the game's file that are
in no list, such as those made in a game started another way, go to the
loose list, to be added to a playset by hand.
"""

from collections.abc import Callable, Collection, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path

import msgspec
import xxhash

from cold_steel.core.build import built_from, built_mods
from cold_steel.core.definitions import digest
from cold_steel.core.deploy import made_from
from cold_steel.core.index import GAME, Index, LayerIndex
from cold_steel.core.mods import base_key
from cold_steel.core.patch import PATCH_PREFIX
from cold_steel.core.playsets import as_played
from cold_steel.paradox.backup import backup_file
from cold_steel.paradox.empires import (
    Empire,
    EmpireFile,
    EmpireInfo,
    EmpireReader,
    Use,
    join_empires,
    parse_empires,
    write_empire_file,
)
from cold_steel.paradox.script import scan
from cold_steel.store.empires import (
    LOOSE,
    EmpireState,
    list_file,
    load_state,
    save_state,
)
from cold_steel.store.files import load_json, write_atomic
from cold_steel.store.playsets import Playset

# The empire file's name when the game hasn't written one yet.
DEFAULT_GAME_FILE = "user_empire_designs_v3.4.txt"


def list_owner(playset: Playset) -> str:
    """Whose list a playset uses: its own, or for a built playset the list of
    the playset it was built from (decision 98)."""
    return next((s for e in playset.entries if (s := built_from(e.key))), playset.id)


def plays_a_build(playset: Playset) -> bool:
    return list_owner(playset) != playset.id


@dataclass(frozen=True)
class Added:
    added: tuple[str, ...] = ()
    replaced: tuple[str, ...] = ()  # one of the same name was there, and is replaced
    skipped: tuple[str, ...] = ()  # one of the same name was there, and stays


class EmpireLists:
    """Every playset's empire list, and the loose one, by owner: a playset id,
    or LOOSE. A list is backed up before each write."""

    def __init__(self, root: Path, backup_dir: Path) -> None:
        self.root = root
        self.backup_dir = backup_dir / "empires"
        self._reader = EmpireReader()

    def path(self, owner: str) -> Path:
        return list_file(owner, self.root)

    def read(self, owner: str) -> EmpireFile:
        """A list, read again only once it changed. Empty when there's none yet.
        Raises OSError if it can't be read."""
        path = self.path(owner)
        if not path.exists():
            return EmpireFile(path, b"", ())
        return self._reader.read(path)

    def owners(self) -> list[str]:
        """Every playset that has a list file, by id."""
        try:
            return sorted(p.stem for p in self.root.glob("*.txt") if p.stem != LOOSE)
        except OSError:
            return []

    def write(self, owner: str, data: bytes) -> None:
        """Replace a list with these bytes, after a backup. Raises OSError."""
        path = self.path(owner)
        backup_file(path, self.backup_dir)
        write_atomic(path, data)

    def add(self, owner: str, empires: Sequence[Empire], *, replace: bool) -> Added:
        """Copy empires into a list. One whose name is in it already replaces
        that one, in its place, if `replace`; otherwise it's skipped."""
        current = self.read(owner)
        incoming = {e.name: e for e in empires}
        have = set(current.names)
        replaced = [n for n in current.names if n in incoming] if replace else []
        texts = [incoming[e.name].text if e.name in replaced else e.text for e in current.empires]
        added = [e for e in incoming.values() if e.name not in have]
        if added or replaced:
            self.write(owner, join_empires(current.head, [*texts, *(e.text for e in added)]))
        skipped = () if replace else tuple(n for n in incoming if n in have)
        return Added(tuple(e.name for e in added), tuple(replaced), skipped)

    def remove(self, owner: str, names: Collection[str]) -> tuple[str, ...]:
        """Take empires out of a list. Returns those taken out."""
        current = self.read(owner)
        going = [e.name for e in current.empires if e.name in names]
        if going:
            staying = [e.text for e in current.empires if e.name not in names]
            self.write(owner, join_empires(current.head, staying))
        return tuple(going)

    def copy_list(self, source: str, target: str) -> None:
        """A copy of a playset gets a copy of its list."""
        path = self.path(source)
        if path.exists():
            self.write(target, path.read_bytes())

    def fingerprints(self) -> set[int]:
        """Every empire in every list, as a hash of its block that ignores spacing."""
        found: set[int] = set()
        for owner in (*self.owners(), LOOSE):
            try:
                found.update(digest(e.text) for e in self.read(owner).empires)
            except OSError:
                continue
        return found


# Which mods an empire needs

# Indexed only as whole files (merge_rules.json), so their objects are read here.
_FILE_FOLDERS = frozenset({"common/species_classes", "common/name_lists"})
_FOLDERS = frozenset(
    {
        "common/species_classes",
        "common/traits",
        "common/name_lists",
        "common/governments",
        "common/governments/authorities",
        "common/governments/civics",
        "common/ethics",
        "common/planet_classes",
        "common/solar_system_initializers",
        "common/graphical_culture",
    }
)
# What each folder holds, as the Empires window names it.
KINDS = {
    "common/species_classes": "species class",
    "common/traits": "trait",
    "common/name_lists": "name list",
    "common/governments": "government",
    "common/governments/authorities": "authority",
    "common/governments/civics": "civic or origin",
    "common/ethics": "ethic",
    "common/planet_classes": "planet class",
    "common/solar_system_initializers": "starting system",
    "common/graphical_culture": "ship set or city look",
}


@dataclass
class Catalog:
    """Which layers (the game, or a mod by key) define each thing an empire can use."""

    defined: dict[Use, frozenset[str]]
    # Each layer's things, kept while the layer is the same object, so the
    # next catalog reads only the mods that changed.
    layers: dict[str, tuple[LayerIndex, frozenset[Use]]] = field(default_factory=dict)


def build_catalog(index: Index, previous: Catalog | None = None) -> Catalog:
    layers: dict[str, tuple[LayerIndex, frozenset[Use]]] = {}
    for key, layer in index.layers.items():
        held = previous.layers.get(key) if previous else None
        if held is not None and held[0] is layer:
            layers[key] = held
        else:
            layers[key] = layer, _layer_uses(index, key, layer)
    found: dict[Use, set[str]] = {}
    for key, (_, uses) in layers.items():
        for use in uses:
            found.setdefault(use, set()).add(key)
    return Catalog({u: frozenset(k) for u, k in found.items()}, layers)


def _layer_uses(index: Index, key: str, layer: LayerIndex) -> frozenset[Use]:
    uses: set[Use] = set()
    for path, entry in layer.files.items():
        folder = path.rpartition("/")[0].lower()
        if folder not in _FOLDERS or not path.lower().endswith(".txt"):
            continue
        if folder in _FILE_FOLDERS:
            data = index.read(key, path) or b""
            uses.update((folder, _text(e.key)) for e in scan(data))
        else:
            uses.update((d.kind, d.key) for d in entry.definitions)
    return frozenset(uses)


@dataclass(frozen=True)
class Need:
    """Something an empire uses that the game doesn't define."""

    use: Use
    mods: tuple[str, ...]  # the mods that define it, by key. Empty: no installed mod does


def _ours(key: str) -> bool:
    return built_from(key) is not None or made_from(key, PATCH_PREFIX) is not None


def empire_needs(info: EmpireInfo, catalog: Catalog) -> tuple[Need, ...]:
    """What an empire uses that only mods define. Any one of a need's mods will do.
    Cold Steel's own builds and patch mods count only when no other mod has it."""
    needs: list[Need] = []
    for use in info.uses:
        found = catalog.defined.get(use, frozenset())
        if GAME in found:
            continue
        theirs = sorted(k for k in found if not _ours(k))
        needs.append(Need(use, tuple(theirs or sorted(found))))
    return tuple(needs)


def playset_mods(playset: Playset, build_dir: Path) -> frozenset[str]:
    """The mods a playset plays, by key. A build counts as every mod inside it."""
    keys: set[str] = set()
    for entry in as_played(playset).entries:
        if not entry.enabled:
            continue
        keys.add(entry.key)
        if source := built_from(entry.key):
            keys.update(built_mods(build_dir, source))
    return frozenset(base_key(k) for k in keys)


@dataclass(frozen=True)
class EmpireCheck:
    """How an empire's needs compare with one playset's mods."""

    missing: tuple[Need, ...] = ()  # needs no mod of the playset meets
    needs_mods: bool = False  # it needs any mod at all

    @property
    def ok(self) -> bool:
        return not self.missing


def check_empire(needs: Iterable[Need], mods: Collection[str]) -> EmpireCheck:
    needs = tuple(needs)
    missing = tuple(n for n in needs if not any(base_key(m) in mods for m in n.mods))
    return EmpireCheck(missing, bool(needs))


def _text(raw: bytes) -> str:
    return raw.strip(b'"').decode("utf-8", "replace")


# The game's empire file, and which list it holds


def game_file_for(found: Path | None, data_dir: Path) -> Path:
    """The game's empire file: the one it uses (empire_file), or the name it
    would get when there's none yet."""
    return found or data_dir / DEFAULT_GAME_FILE


def _read(path: Path) -> bytes:
    try:
        return path.read_bytes()
    except FileNotFoundError:
        return b""


def _hash(data: bytes) -> int:
    return xxhash.xxh3_64_intdigest(data)


def shelve_outside_changes(
    lists: EmpireLists, game_file: Path, state_path: Path
) -> tuple[str, ...]:
    """If the game's empire file changed since Cold Steel last wrote or read it,
    as when the game was started from the launcher, copy each empire in it that
    no list has to the loose list. Nothing is lost, and no list is changed
    behind your back. Returns the names copied. Raises OSError."""
    state = load_state(state_path)
    if state.running:
        return ()  # the file holds a list now: take_back first
    data = _read(game_file)
    if state.file == str(game_file) and state.digest == _hash(data):
        return ()
    known = lists.fingerprints()
    found = [e for e in parse_empires(game_file, data).empires if digest(e.text) not in known]
    if found:
        lists.add(LOOSE, found, replace=True)
    save_state(msgspec.structs.replace(state, file=str(game_file), digest=_hash(data)), state_path)
    return tuple(e.name for e in found)


def put_in_game(
    lists: EmpireLists, owner: str, game_file: Path, state_path: Path, backup_dir: Path
) -> None:
    """Make a playset's list the game's empire file, after a backup. Changes
    made outside Cold Steel are kept first (shelve_outside_changes). An empty
    list makes an empty file, so the game shows only its own empires.
    Raises OSError; the game's file is then as it was."""
    shelve_outside_changes(lists, game_file, state_path)
    data = _read(lists.path(owner))
    write_empire_file(game_file, data, backup_dir)
    # Written after the file, so a crash in between can't copy another list's
    # empires into this one: the next start sees an outside change instead.
    save_state(
        EmpireState(owner=owner, file=str(game_file), digest=_hash(data), running=True), state_path
    )


@dataclass(frozen=True)
class TakenBack:
    owner: str  # whose list it was
    new: tuple[str, ...] = ()  # empires made or changed in game
    loose: bool = False  # the playset was deleted meanwhile: they went to the loose list


def take_back(
    lists: EmpireLists, state_path: Path, exists: Callable[[str], bool]
) -> TakenBack | None:
    """After the game closed: copy the game's empire file back into the list
    Play put in, byte for byte, so what was made, changed or deleted in game is
    kept. If that playset was deleted meanwhile, its empires go to the loose
    list. None when Play put nothing in. Raises OSError; the state then stays,
    to try again."""
    state = load_state(state_path)
    if not state.running:
        return None
    game_file = Path(state.file)
    data = _read(game_file)
    now = parse_empires(game_file, data)
    owner = state.owner
    before = fingerprints(lists.read(owner)) if exists(owner) else {}
    if exists(owner):
        if data != _read(lists.path(owner)):
            lists.write(owner, data)
    elif now.empires:
        lists.add(LOOSE, now.empires, replace=True)
    save_state(EmpireState(owner=owner, file=str(game_file), digest=_hash(data)), state_path)
    return TakenBack(owner, tuple(changed_since(before, now)), not exists(owner))


def fingerprints(file: EmpireFile | None) -> dict[str, int]:
    """Each empire's name -> a hash of its block that ignores spacing."""
    return {e.name: digest(e.text) for e in file.empires} if file else {}


def changed_since(before: Mapping[str, int], now: EmpireFile | None) -> list[str]:
    """The empires that are new, or changed, since `before` (fingerprints)."""
    return [n for n, d in fingerprints(now).items() if before.get(n) != d]


def in_game(state_path: Path) -> str:
    """The id of the playset whose list the game holds while it runs, or ""."""
    state = load_state(state_path)
    return state.owner if state.running else ""


# The first lists


class _OldBinding(msgspec.Struct):
    playsets: tuple[str, ...] = ()


class _OldBindings(msgspec.Struct):
    empires: dict[str, _OldBinding] = msgspec.field(default_factory=dict)


def first_lists(
    lists: EmpireLists,
    game_file: Path,
    state_path: Path,
    old_bindings: Path,
    owners: Mapping[str, str],
) -> bool:
    """The first time: make the lists from the game's empire file. An empire
    bound to playsets by an earlier build of Phase 9 goes to each of their
    lists; the rest go to the loose list. `owners` maps each playset id to its
    list's owner (list_owner). Returns True if it ran. Raises OSError."""
    if lists.root.exists():
        return False
    data = _read(game_file)
    old = load_json(old_bindings, _OldBindings) or _OldBindings()
    lists.root.mkdir(parents=True)
    by_owner: dict[str, list[Empire]] = {}
    for empire in parse_empires(game_file, data).empires:
        bound = old.empires.get(empire.name)
        homes = dict.fromkeys(owners[p] for p in (bound.playsets if bound else ()) if p in owners)
        for owner in homes or (LOOSE,):
            by_owner.setdefault(owner, []).append(empire)
    for owner, empires in by_owner.items():
        lists.add(owner, empires, replace=True)
    if old_bindings.exists():
        old_bindings.replace(old_bindings.with_name(f"{old_bindings.name}.old"))
    save_state(EmpireState(file=str(game_file), digest=_hash(data)), state_path)
    return True
