"""Empires: which playsets each belongs to, which mods each needs, and hiding
other playsets' empires from the game while it runs.

    book = EmpireBook.open(path)
    book.bind(["Divine Elven Order"], playset.id)
    catalog = build_catalog(index)
    check = check_empire(empire_needs(info, catalog), playset_mods(playset, build_dir))
    hide_empires(empires_to_hide(book, file.names, shown_for(playset), ids), …)

An empire is one block in the game's empire file (paradox/empires.py), named
by its key. It can belong to several playsets (decision 96), and one bound to
none shows in every playset. A built playset also shows the empires of the
playset it was built from (decision 98).
"""

from collections.abc import Collection, Iterable, Mapping
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import msgspec

from cold_steel.core.build import built_from, built_mods
from cold_steel.core.definitions import digest
from cold_steel.core.deploy import made_from
from cold_steel.core.index import GAME, Index, LayerIndex
from cold_steel.core.mods import base_key
from cold_steel.core.patch import PATCH_PREFIX
from cold_steel.core.playsets import as_played
from cold_steel.paradox.empires import (
    EmpireFile,
    EmpireInfo,
    Use,
    join_empires,
    read_empire_file,
    split_empires,
    write_empire_file,
)
from cold_steel.paradox.script import scan
from cold_steel.store import paths
from cold_steel.store.empires import (
    EmpireBinding,
    EmpireBindingsFile,
    load_empire_bindings,
    save_empire_bindings,
)
from cold_steel.store.files import write_atomic
from cold_steel.store.playsets import Playset


class EmpireBook:
    """Which playsets each empire belongs to, by name. Every change is saved at once."""

    def __init__(self, data: EmpireBindingsFile, path: Path) -> None:
        self._data = data
        self._path = path
        self.problems: list[str] = []

    @classmethod
    def open(cls, path: Path) -> EmpireBook:
        """A file that exists but can't be read is renamed, not overwritten."""
        data = load_empire_bindings(path)
        problems: list[str] = []
        if data is None and path.exists():
            stamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
            kept = path.with_name(f"{path.stem}.unreadable-{stamp}{path.suffix}")
            path.replace(kept)
            problems.append(f"Your empires file couldn't be read. It was kept as {kept.name}.")
        book = cls(data or EmpireBindingsFile(), path)
        book.problems = problems
        return book

    def get(self, name: str) -> EmpireBinding | None:
        return self._data.empires.get(name)

    def playsets_of(self, name: str) -> tuple[str, ...]:
        binding = self._data.empires.get(name)
        return binding.playsets if binding else ()

    def count(self, playset_id: str) -> int:
        """How many empires are bound to a playset."""
        return sum(playset_id in b.playsets for b in self._data.empires.values())

    def bind(self, names: Iterable[str], playset_id: str) -> None:
        """Bind empires to a playset, as well as any they're bound to already."""
        bound = datetime.now().strftime("%Y-%m-%d %H:%M")
        for name in names:
            have = self.playsets_of(name)
            playsets = have if playset_id in have else (*have, playset_id)
            self._data.empires[name] = EmpireBinding(playsets, bound)
        self._save()

    def unbind(self, names: Iterable[str], playset_id: str) -> None:
        """Unbind empires from one playset, by choice. One left with none is
        never suggested for a playset again."""
        for name in names:
            if binding := self._data.empires.get(name):
                playsets = tuple(p for p in binding.playsets if p != playset_id)
                self._data.empires[name] = msgspec.structs.replace(binding, playsets=playsets)
        self._save()

    def forget(self, names: Iterable[str]) -> None:
        """The empires were deleted: forget their bindings."""
        for name in names:
            self._data.empires.pop(name, None)
        self._save()

    def forget_playset(self, playset_id: str) -> int:
        """The playset is gone: its empires lose that binding. One bound to
        nothing else becomes unbound, and may be suggested for another playset.
        How many empires it had."""
        gone = 0
        for name, binding in list(self._data.empires.items()):
            if playset_id not in binding.playsets:
                continue
            gone += 1
            playsets = tuple(p for p in binding.playsets if p != playset_id)
            if playsets:
                self._data.empires[name] = msgspec.structs.replace(binding, playsets=playsets)
            else:
                del self._data.empires[name]
        if gone:
            self._save()
        return gone

    def copy_playset(self, source_id: str, copy_id: str) -> None:
        """A copy of a playset gets the same empires."""
        for name, binding in self._data.empires.items():
            if source_id in binding.playsets and copy_id not in binding.playsets:
                self._data.empires[name] = msgspec.structs.replace(
                    binding, playsets=(*binding.playsets, copy_id)
                )
        self._save()

    def _save(self) -> None:
        save_empire_bindings(self._data, self._path)


# Which empires show where


def shown_for(playset: Playset) -> frozenset[str]:
    """The playsets whose empires show in this one: itself, and for a built
    playset the playset it was built from (decision 98)."""
    sources = {s for e in playset.entries if (s := built_from(e.key))}
    return frozenset({playset.id, *sources})


def plays_a_build(playset: Playset) -> bool:
    return any(built_from(e.key) for e in playset.entries)


def bound_empires(book: EmpireBook, names: Iterable[str], shown: Collection[str]) -> list[str]:
    """The empires bound to any of the `shown` playsets, in file order."""
    return [n for n in names if any(p in shown for p in book.playsets_of(n))]


def unbound_empires(
    book: EmpireBook, names: Iterable[str], playset_ids: Collection[str]
) -> list[str]:
    """The empires bound to no playset that exists. They show in every playset."""
    return [n for n in names if not any(p in playset_ids for p in book.playsets_of(n))]


def empires_to_hide(
    book: EmpireBook, names: Iterable[str], shown: Collection[str], playset_ids: Collection[str]
) -> list[str]:
    """The empires to keep out of the game: bound only to other playsets that exist."""
    found: list[str] = []
    for name in names:
        existing = [p for p in book.playsets_of(name) if p in playset_ids]
        if existing and not any(p in shown for p in existing):
            found.append(name)
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


def suggest_empires(
    empires: Iterable[tuple[str, tuple[Need, ...]]],
    book: EmpireBook,
    playsets: Mapping[str, frozenset[str]],
) -> dict[str, tuple[str, ...]]:
    """Empire name -> the ids of the playsets that have every mod it needs.

    `playsets` is each playset's mods (playset_mods), by id: built playsets
    are left out by the caller. Only for empires never bound, or bound to
    playsets that are gone. An empire that needs no mod works everywhere, and
    one that needs a mod nobody has works nowhere: neither is suggested."""
    found: dict[str, tuple[str, ...]] = {}
    for name, needs in empires:
        binding = book.get(name)
        if binding is not None and (
            not binding.playsets or any(p in playsets for p in binding.playsets)
        ):
            continue  # unbound by choice, or bound
        if not needs or any(not n.mods for n in needs):
            continue
        fits = tuple(pid for pid, mods in playsets.items() if check_empire(needs, mods).ok)
        if fits:
            found[name] = fits
    return found


# Binding by Play


def _text(raw: bytes) -> str:
    return raw.strip(b'"').decode("utf-8", "replace")


def fingerprints(file: EmpireFile | None) -> dict[str, int]:
    """Each empire's name -> a hash of its block that ignores spacing."""
    return {e.name: digest(e.text) for e in file.empires} if file else {}


def changed_since(before: Mapping[str, int], now: EmpireFile | None) -> list[str]:
    """The empires that are new, or changed, since `before` (fingerprints)."""
    return [n for n, d in fingerprints(now).items() if before.get(n) != d]


# Hiding other playsets' empires while the game runs

_PATH_LINE = b"# Cold Steel: empires hidden while the game runs. They go back into:\n# "


def hidden_empires_file() -> Path:
    return paths.data_dir() / "hidden_empires.txt"


@dataclass(frozen=True)
class HiddenEmpires:
    path: Path | None  # the empire file they came from
    texts: tuple[tuple[str, bytes], ...]  # each hidden block, by name, byte for byte


def read_hidden(record: Path) -> HiddenEmpires:
    """What `hidden_empires.txt` holds. Nothing if it's missing."""
    try:
        data = record.read_bytes()
    except OSError:
        return HiddenEmpires(None, ())
    path: Path | None = None
    if data.startswith(_PATH_LINE):
        line, _, data = data[len(_PATH_LINE) :].partition(b"\n")
        path = Path(line.decode("utf-8", "surrogateescape"))
    _, blocks = split_empires(data)
    return HiddenEmpires(path, tuple(blocks))


def hidden_names(record: Path) -> tuple[str, ...]:
    return tuple(name for name, _ in read_hidden(record).texts)


def _write_hidden(record: Path, path: Path, texts: Iterable[bytes]) -> None:
    head = _PATH_LINE + str(path).encode("utf-8", "surrogateescape") + b"\n"
    write_atomic(record, join_empires(head, texts))


def hide_empires(
    names: Collection[str], path: Path, record: Path, backup_dir: Path
) -> tuple[str, ...]:
    """Take these empires out of the empire file at `path`, keeping their blocks
    in `record` first, byte for byte. Returns those taken out. An empire whose
    name is still in the record, not put back, stays where it is.

    Raises OSError if anything can't be written. The empire file is then as it was.
    """
    if not names or not path.is_file():
        return ()
    current = read_empire_file(path)
    held = read_hidden(record)
    if held.texts and held.path != path:
        return ()  # still hidden from another empire file: theirs to put back first
    kept_names = {n for n, _ in held.texts}
    taking = [e for e in current.empires if e.name in names and e.name not in kept_names]
    if not taking:
        return ()
    taken = {e.name for e in taking}
    _write_hidden(record, path, [*(t for _, t in held.texts), *(e.text for e in taking)])
    staying = [e.text for e in current.empires if e.name not in taken]
    try:
        write_empire_file(path, join_empires(current.head, staying), backup_dir)
    except OSError:
        if held.texts:
            _write_hidden(record, path, (t for _, t in held.texts))
        else:
            record.unlink(missing_ok=True)
        raise
    return tuple(e.name for e in taking)


@dataclass(frozen=True)
class RestoredEmpires:
    back: tuple[str, ...] = ()  # put back into the empire file
    # Still hidden: an empire of the same name is in the file by then. They
    # stay in the record, and nothing is overwritten.
    stuck: tuple[str, ...] = ()
    record: Path = field(default_factory=Path)


def restore_empires(record: Path, backup_dir: Path) -> RestoredEmpires:
    """Add every hidden empire back to whatever the game left in the empire
    file. One whose name is there by then stays hidden, unless it's the same
    block, as when Cold Steel stopped just after putting it back.

    Raises OSError if the empire file can't be written; the record then stays."""
    held = read_hidden(record)
    if not held.texts or held.path is None:
        return RestoredEmpires(record=record)
    path = held.path
    try:
        data = path.read_bytes()
    except FileNotFoundError:
        data = b""  # every empire was deleted in the game
    now = {name: digest(text) for name, text in split_empires(data)[1]}
    back: list[str] = []
    adding: list[bytes] = []
    stuck: list[tuple[str, bytes]] = []
    for name, text in held.texts:
        if name not in now:
            adding.append(text)
            back.append(name)
        elif now[name] == digest(text):
            back.append(name)
        else:
            stuck.append((name, text))
    if adding:
        write_empire_file(path, join_empires(data, adding), backup_dir)
    if stuck:
        _write_hidden(record, path, (t for _, t in stuck))
    else:
        record.unlink(missing_ok=True)
    return RestoredEmpires(tuple(back), tuple(n for n, _ in stuck), record)


def remove_empires(names: Collection[str], path: Path, backup_dir: Path) -> tuple[str, ...]:
    """Delete empires from the empire file, after a backup. Returns those deleted.
    Raises OSError if it can't be written."""
    current = read_empire_file(path)
    going = [e for e in current.empires if e.name in names]
    if going:
        staying = [e.text for e in current.empires if e.name not in names]
        write_empire_file(path, join_empires(current.head, staying), backup_dir)
    return tuple(e.name for e in going)
