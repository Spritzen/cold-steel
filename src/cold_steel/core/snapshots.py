"""Pinned copies of Workshop mods, so a playset keeps playing the versions it
was pinned to, whatever Steam does.

    store = SnapshotStore(snapshots_dir())
    taken = Pinner(store, library, keys, previous)(ctx)  # {mod key: Snapshot}
    drift = PinChecker(store, library, ids)(ctx)  # what Steam changed since

Steam changes a Workshop folder whenever an author updates, and deletes it
when you unsubscribe (STG paid for learning this). A pinned copy changes only
when you accept an update.

Layout, in `~/.local/share/cold-steel/snapshots/` (decision 50):

    objects/ab/<hash>   each file's bytes, kept once however many copies use it
    mods/<id>/          one pinned copy: hard links into objects/
    mods/<id>.json      its record: which mod, when, and every file's hash

Files come in from Steam's folder as reflinks where the disk supports them,
else as real copies. Never as hard links: Steam could rewrite a file in place,
and that would change the pinned copy too. Objects are read-only, and a copy's
files are hard links to them, so two copies of a mod share every file they
have in common. An object nothing links to any more is deleted by collect().

The game loads a pinned copy through its own .mod file,
`mod/cold_steel_pin_<Workshop id>_<id>.mod`, which the scan reads as the
pinned Mod `workshop:<Workshop id>@<id>`.
"""

import contextlib
import os
import shutil
import tempfile
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import msgspec
import xxhash

from cold_steel.core.deploy import clone_file, link_or_clone, write_outer
from cold_steel.core.jobs import JobContext
from cold_steel.core.library import Library
from cold_steel.core.mods import PIN_PREFIX, Mod, Stamp, _walk
from cold_steel.paradox.descriptor import Descriptor
from cold_steel.paradox.game import Game
from cold_steel.store import paths
from cold_steel.store.files import load_json, save_json

CHUNK = 1 << 20


class SnapshotError(Exception):
    pass


class SnapshotFile(msgspec.Struct, frozen=True, array_like=True):
    hash: str  # xxh3-128 of the bytes, as hex: the object's name
    size: int
    mtime: int  # Steam's file's timestamp when it was copied, in nanoseconds


class Snapshot(msgspec.Struct, frozen=True):
    id: str
    key: str  # the Workshop mod's Mod.key
    name: str
    version: str
    taken: str  # when, as "2026-10-02 14:03"
    files: dict[str, SnapshotFile]  # by path inside the mod
    archive: str = ""  # for a mod shipped as a zip: the zip, by path inside the copy

    @property
    def size(self) -> int:
        return sum(f.size for f in self.files.values())


@dataclass(frozen=True)
class Drift:
    """What Steam changed in a pinned mod since the copy was taken."""

    added: tuple[str, ...] = ()
    removed: tuple[str, ...] = ()
    changed: tuple[str, ...] = ()
    gone: bool = False  # Steam's folder isn't there: unsubscribed, or deleted
    version: str = ""  # the version Steam has now

    @property
    def updated(self) -> bool:
        return bool(self.added or self.removed or self.changed)


def snapshots_dir() -> Path:
    return paths.data_dir() / "snapshots"


def pin_name(key: str, snapshot: str) -> str:
    """The name of a pinned copy's .mod file, without ".mod"."""
    return f"{PIN_PREFIX}{key.partition(':')[2]}_{snapshot}"


class SnapshotStore:
    def __init__(self, root: Path) -> None:
        self.root = root
        self._objects = root / "objects"
        self._mods = root / "mods"

    def folder(self, snapshot: str) -> Path:
        return self._mods / snapshot

    def load(self, snapshot: str) -> Snapshot | None:
        return load_json(self._mods / f"{snapshot}.json", Snapshot)

    def ids(self) -> list[str]:
        """Every snapshot the store holds."""
        try:
            return sorted(p.stem for p in self._mods.glob("*.json"))
        except OSError:
            return []

    # Taking a copy

    def take(self, mod: Mod, previous: Snapshot | None, ctx: JobContext) -> Snapshot:
        """Copy a Workshop mod into the store as it is now.

        A file with the same size and timestamp as in `previous` isn't read
        again: its object is reused. Raises SnapshotError if Steam changes a
        file while it's being copied.
        """
        if not mod.root or not Path(mod.root).is_dir():
            raise SnapshotError(f"{mod.name} isn't in Steam's folder, so it can't be copied.")
        root = Path(mod.root)
        files: dict[str, SnapshotFile] = {}
        for path, stamp in sorted(_walk(root).items()):
            ctx.check_cancelled()
            old = previous.files.get(path) if previous else None
            if (
                old is not None
                and (old.size, old.mtime) == stamp
                and self._object(old.hash).exists()
            ):
                files[path] = old
            else:
                files[path] = self._store(root / path, mod.name)

        snapshot_id = _snapshot_id(mod.key, files)
        folder = self.folder(snapshot_id)
        if not folder.is_dir():
            new = self._mods / f".{snapshot_id}.new"
            shutil.rmtree(new, ignore_errors=True)
            for path, file in files.items():
                target = new / path
                target.parent.mkdir(parents=True, exist_ok=True)
                link_or_clone(self._object(file.hash), target)
            new.mkdir(parents=True, exist_ok=True)
            new.rename(folder)

        archive = ""
        if mod.archive and Path(mod.archive).is_relative_to(root):
            archive = Path(mod.archive).relative_to(root).as_posix()
        snapshot = Snapshot(
            id=snapshot_id,
            key=mod.key,
            name=mod.name,
            version=mod.version,
            taken=datetime.now().strftime("%Y-%m-%d %H:%M"),
            files=files,
            archive=archive,
        )
        old_record = self.load(snapshot_id)
        if old_record is not None and old_record.files == files:
            return old_record  # the same copy, taken before: keep its date
        save_json(self._mods / f"{snapshot_id}.json", snapshot)
        return snapshot

    def _store(self, src: Path, name: str) -> SnapshotFile:
        """Copy one file into objects/. It's hashed after copying, so an
        object's bytes always match its name."""
        self._objects.mkdir(parents=True, exist_ok=True)
        before = src.stat()
        fd, tmp_name = tempfile.mkstemp(dir=self._objects, prefix=".incoming-")
        os.close(fd)
        tmp = Path(tmp_name)
        try:
            clone_file(src, tmp)
            after = src.stat()
            if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
                raise SnapshotError(
                    f"Steam changed {name} while it was being copied. Wait for Steam, then "
                    "try again."
                )
            digest = hash_file(tmp)
            target = self._object(digest)
            if target.exists():
                tmp.unlink()
            else:
                target.parent.mkdir(exist_ok=True)
                tmp.chmod(0o444)
                tmp.replace(target)
        except BaseException:
            tmp.unlink(missing_ok=True)
            raise
        return SnapshotFile(digest, before.st_size, before.st_mtime_ns)

    def _object(self, digest: str) -> Path:
        return self._objects / digest[:2] / digest

    # Comparing with Steam's folder

    def drift(self, snapshot: Snapshot, mod: Mod | None, stamps: Mapping[str, Stamp]) -> Drift:
        """What changed in Steam's folder since the copy. A file whose
        timestamp changed is read, so one Steam only touched doesn't count."""
        if mod is None or not mod.installed or not mod.root:
            return Drift(gone=True)
        root = Path(mod.root)
        added = sorted(set(stamps) - set(snapshot.files))
        removed = sorted(set(snapshot.files) - set(stamps))
        changed: list[str] = []
        for path in sorted(set(stamps) & set(snapshot.files)):
            file = snapshot.files[path]
            size, mtime = stamps[path]
            if (size, mtime) == (file.size, file.mtime):
                continue
            try:
                same = size == file.size and hash_file(root / path) == file.hash
            except OSError:
                same = False
            if not same:
                changed.append(path)
        return Drift(tuple(added), tuple(removed), tuple(changed), version=mod.version)

    # Deleting what nothing uses

    def collect(self, keep: Iterable[str], game: Game | None) -> int:
        """Delete every snapshot not in `keep`, with its .mod file, then every
        object no copy or build links to. Returns the bytes freed."""
        keep = set(keep)
        for snapshot in self.ids():
            if snapshot in keep:
                continue
            shutil.rmtree(self.folder(snapshot), ignore_errors=True)
            (self._mods / f"{snapshot}.json").unlink(missing_ok=True)
            if game is not None:
                for outer in game.mod_dir.glob(f"{PIN_PREFIX}*_{snapshot}.mod"):
                    outer.unlink(missing_ok=True)
        with contextlib.suppress(OSError):
            for stray in self._mods.glob(".*.new"):
                shutil.rmtree(stray, ignore_errors=True)
            for stray in self._objects.glob(".incoming-*"):  # left by a copy that stopped
                stray.unlink(missing_ok=True)
        freed = 0
        try:
            objects = [p for p in self._objects.glob("*/*") if p.is_file()]
        except OSError:
            return 0
        for path in objects:
            with contextlib.suppress(OSError):
                st = path.stat()
                if st.st_nlink == 1:  # only the store has it
                    path.unlink()
                    freed += st.st_size
        return freed

    # The game's .mod file

    def write_outer(self, snapshot: Snapshot, mod: Mod, game: Game) -> Path:
        """The .mod file that makes the game load this copy."""
        folder = self.folder(snapshot.id)
        descriptor = Descriptor(
            name=mod.name,
            version=snapshot.version,
            supported_version=mod.supported_version,
            tags=mod.tags,
            dependencies=mod.dependencies,
            path="" if snapshot.archive else str(folder),
            archive=str(folder / snapshot.archive) if snapshot.archive else "",
        )
        outer = game.mod_dir / f"{pin_name(snapshot.key, snapshot.id)}.mod"
        write_outer(outer, descriptor)
        return outer


# Jobs


@dataclass
class Pinner:
    """Copy Workshop mods into the store. Call it with a JobContext to run it.

    `previous` maps a mod's key to the snapshot it's pinned to now, if any,
    so files that didn't change aren't read again.
    """

    store: SnapshotStore
    library: Library
    keys: tuple[str, ...]
    previous: Mapping[str, str]

    def __call__(self, ctx: JobContext) -> dict[str, Snapshot]:
        mods = {m.key: m for m in self.library.mods}
        taken: dict[str, Snapshot] = {}
        for done, key in enumerate(self.keys):
            mod = mods.get(key)
            if mod is None:
                continue
            ctx.progress(done, len(self.keys), f"Saving a copy of {mod.name}")
            old = self.previous.get(key)
            previous = self.store.load(old) if old else None
            snapshot = self.store.take(mod, previous, ctx)
            self.store.write_outer(snapshot, mod, self.library.game)
            taken[key] = snapshot
        return taken


@dataclass
class PinChecker:
    """What Steam changed in each pinned mod. Call it with a JobContext to run it."""

    store: SnapshotStore
    library: Library
    snapshots: tuple[str, ...]

    def __call__(self, ctx: JobContext) -> dict[str, tuple[Snapshot, Drift]]:
        mods = {m.key: m for m in self.library.mods}
        found: dict[str, tuple[Snapshot, Drift]] = {}
        for done, snapshot_id in enumerate(self.snapshots):
            ctx.progress(done, len(self.snapshots), "Checking pinned mods for updates")
            snapshot = self.store.load(snapshot_id)
            if snapshot is None:
                continue
            mod = mods.get(snapshot.key)
            stamps = self.library.files.get(snapshot.key, {})
            found[snapshot_id] = (snapshot, self.store.drift(snapshot, mod, stamps))
        return found


def hash_file(path: Path) -> str:
    digest = xxhash.xxh3_128()
    with path.open("rb") as f:
        while chunk := f.read(CHUNK):
            digest.update(chunk)
    return digest.hexdigest()


def _snapshot_id(key: str, files: Mapping[str, SnapshotFile]) -> str:
    data = msgspec.msgpack.encode([key, sorted((p, f.hash) for p, f in files.items())])
    return f"{xxhash.xxh3_64_intdigest(data):016x}"
