"""Our playsets, and every change made to them.

    book = PlaysetBook.open(path, library)  # first run: imports the launcher's
    playset = book.create("New playset")
    book.update(add_mods(playset, ["workshop:123"], library))

Every change is saved at once. The file is small, so this takes milliseconds.
"""

import uuid
from collections.abc import Iterable, Sequence
from datetime import datetime
from pathlib import Path

import msgspec

from cold_steel.core.library import Library
from cold_steel.core.mods import Mod
from cold_steel.store.playsets import (
    Playset,
    PlaysetEntry,
    PlaysetFile,
    load_playsets,
    save_playsets,
)


class PlaysetBook:
    def __init__(self, data: PlaysetFile, path: Path) -> None:
        self._data = data
        self._path = path
        self.problems: list[str] = []

    @classmethod
    def open(cls, path: Path, library: Library | None = None) -> PlaysetBook:
        """Load our playsets. The first time, start with copies of the launcher's.

        A file that exists but can't be read is renamed, not overwritten, so
        nothing in it is lost.
        """
        data = load_playsets(path)
        problems: list[str] = []
        if data is None and path.exists():
            stamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
            kept = path.with_name(f"{path.stem}.unreadable-{stamp}{path.suffix}")
            path.replace(kept)
            problems.append(f"Your playsets file couldn't be read. It was kept as {kept.name}.")
        if data is None:
            data = PlaysetFile()
            if library is not None:
                data.playsets = list(library.launcher_playsets)
                data.active = library.launcher_active
            save_playsets(data, path)
        book = cls(data, path)
        book.problems = problems
        return book

    @property
    def playsets(self) -> tuple[Playset, ...]:
        return tuple(self._data.playsets)

    @property
    def active(self) -> str:
        return self._data.active

    def get(self, playset_id: str) -> Playset | None:
        return next((p for p in self._data.playsets if p.id == playset_id), None)

    def create(self, name: str, entries: Sequence[PlaysetEntry] = ()) -> Playset:
        playset = Playset(id=new_id(), name=name, entries=tuple(entries))
        self._data.playsets.append(playset)
        self._save()
        return playset

    def add(self, playset: Playset) -> Playset:
        """Add a playset made elsewhere (imported, or loaded from a file) under a new id."""
        playset = msgspec.structs.replace(playset, id=new_id())
        self._data.playsets.append(playset)
        self._save()
        return playset

    def copy(self, playset_id: str, name: str) -> Playset:
        original = self._require(playset_id)
        copy = msgspec.structs.replace(original, id=new_id(), name=name, launcher_id="")
        index = self._data.playsets.index(original)
        self._data.playsets.insert(index + 1, copy)
        self._save()
        return copy

    def update(self, playset: Playset) -> None:
        """Replace the playset with the same id."""
        index = self._data.playsets.index(self._require(playset.id))
        self._data.playsets[index] = playset
        self._save()

    def rename(self, playset_id: str, name: str) -> Playset:
        playset = msgspec.structs.replace(self._require(playset_id), name=name)
        self.update(playset)
        return playset

    def delete(self, playset_id: str) -> None:
        self._data.playsets.remove(self._require(playset_id))
        if self._data.active == playset_id:
            self._data.active = ""
        self._save()

    def set_active(self, playset_id: str) -> None:
        self._require(playset_id)
        self._data.active = playset_id
        self._save()

    def unused_name(self, wanted: str) -> str:
        """`wanted`, or "wanted (2)", "wanted (3)"… if that name is taken."""
        names = {p.name for p in self._data.playsets}
        name, n = wanted, 1
        while name in names:
            n += 1
            name = f"{wanted} ({n})"
        return name

    def _require(self, playset_id: str) -> Playset:
        playset = self.get(playset_id)
        if playset is None:
            raise KeyError(f"No playset with id {playset_id}")
        return playset

    def _save(self) -> None:
        save_playsets(self._data, self._path)


def new_id() -> str:
    return str(uuid.uuid4())


# Changing one playset. Each returns a new Playset; pass it to PlaysetBook.update.


def add_mods(playset: Playset, keys: Iterable[str], library: Library) -> Playset:
    """Add mods at the end, turned on. Mods already in the playset stay where they are."""
    names = {m.key: m.name for m in library.mods}
    present = {e.key for e in playset.entries}
    new = [PlaysetEntry(k, True, names.get(k, "")) for k in dict.fromkeys(keys) if k not in present]
    return msgspec.structs.replace(playset, entries=playset.entries + tuple(new))


def remove_mods(playset: Playset, keys: Iterable[str]) -> Playset:
    gone = set(keys)
    return msgspec.structs.replace(
        playset, entries=tuple(e for e in playset.entries if e.key not in gone)
    )


def set_enabled(playset: Playset, keys: Iterable[str], enabled: bool) -> Playset:
    chosen = set(keys)
    return msgspec.structs.replace(
        playset,
        entries=tuple(
            msgspec.structs.replace(e, enabled=enabled) if e.key in chosen else e
            for e in playset.entries
        ),
    )


def move_mods(playset: Playset, keys: Sequence[str], before: str | None) -> Playset:
    """Move mods so they sit just before `before`, keeping their own order.
    `before=None` moves them to the end. If `before` is one of the mods being
    moved, they go before the next mod that isn't."""
    chosen = set(keys)
    keys_in_order = [e.key for e in playset.entries]
    if before in chosen:
        after = keys_in_order[keys_in_order.index(before) :]
        before = next((k for k in after if k not in chosen), None)
    moving = [e for e in playset.entries if e.key in chosen]
    staying = [e for e in playset.entries if e.key not in chosen]
    at = next((i for i, e in enumerate(staying) if e.key == before), len(staying))
    return msgspec.structs.replace(playset, entries=tuple(staying[:at] + moving + staying[at:]))


def set_dlc_enabled(playset: Playset, folder: str, enabled: bool) -> Playset:
    disabled = set(playset.disabled_dlcs)
    if enabled:
        disabled.discard(folder)
    else:
        disabled.add(folder)
    return msgspec.structs.replace(playset, disabled_dlcs=tuple(sorted(disabled)))


def missing_mods(playsets: Iterable[Playset], library: Library) -> list[Mod]:
    """A stand-in Mod for each playset entry that isn't installed any more."""
    installed = {m.key for m in library.mods}
    missing: dict[str, Mod] = {}
    for playset in playsets:
        for entry in playset.entries:
            if entry.key in installed or entry.key in missing:
                continue
            source, _, ident = entry.key.partition(":")
            missing[entry.key] = Mod(
                key=entry.key,
                source="workshop" if source == "workshop" else "local",
                name=entry.name or ident or entry.key,
                remote_file_id=ident if source == "workshop" else "",
                installed=False,
            )
    return list(missing.values())
