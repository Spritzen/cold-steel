"""The object index: every file in the game and in each installed mod, and the
objects each file defines.

    index = Indexer(library, cache_dir)(ctx)
    index.layers["workshop:123"].files["common/technology/x.txt"].definitions

Speed rules (decision 9):
- A mod whose files all kept their size and timestamp isn't looked at again.
- A changed file is hashed with xxhash. If the hash is the same, its old
  definitions are kept.
- Only files that really changed are parsed, in parallel across CPU cores.

Each mod's index is saved to its own file in `~/.cache/cold-steel/index/`, so
changing one mod rewrites one small file.
"""

import os
import zipfile
from collections.abc import Iterator, Mapping
from concurrent.futures import Future, ProcessPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path

import msgspec
import xxhash

from cold_steel.core.definitions import Definition, read_definitions, worth_reading
from cold_steel.core.jobs import JobContext
from cold_steel.core.library import Library
from cold_steel.core.merge_rules import load_rules
from cold_steel.core.mods import Stamp, _walk
from cold_steel.paradox.localisation import game_language
from cold_steel.store.files import load_msgpack, save_msgpack

# Bump when Definition, IndexedFile or the parsing changes shape, so old
# indexes are thrown away. Changing merge_rules.json does this by itself.
INDEX_VERSION = 1

GAME = "game"  # the layer key for the game's own files
# The game's folders that hold content. The rest of its install is programs.
GAME_FOLDERS = (
    "common",
    "events",
    "flags",
    "fonts",
    "gfx",
    "interface",
    "localisation",
    "localisation_synced",
    "map",
    "music",
    "prescripted_countries",
    "sound",
)
_CONTENT = frozenset(GAME_FOLDERS)
# Hand this much parsing to each worker at a time.
CHUNK_BYTES = 2_000_000
# Below this much, starting worker processes costs more than it saves.
PARALLEL_BYTES = 16_000_000


class IndexedFile(msgspec.Struct, frozen=True, array_like=True):
    stamp: Stamp
    hash: int = 0  # xxhash of the bytes; 0 for a file that isn't read inside
    definitions: tuple[Definition, ...] = ()


class LayerIndex(msgspec.Struct, frozen=True):
    """The game's files, or one mod's."""

    fingerprint: int  # changes when any file's size or timestamp does
    settings: int  # changes with INDEX_VERSION, the rules table and the language
    files: dict[str, IndexedFile]  # by path inside the mod, as written


@dataclass(frozen=True)
class Source:
    """Where a layer's files are: a folder, or a zip."""

    key: str
    root: Path | None
    archive: Path | None
    stamps: Mapping[str, Stamp]  # what the scan saw; for a zip, just the zip


type ObjectKey = tuple[str, str]  # kind, key


@dataclass(frozen=True)
class Tables:
    """One layer's files and objects, arranged for finding conflicts."""

    paths: dict[str, str]  # every content file: lowercased path -> path as written
    objects: dict[ObjectKey, list[tuple[str, Definition]]]  # -> (path, definition)


@dataclass(frozen=True)
class Index:
    language: str
    layers: dict[str, LayerIndex]
    sources: dict[str, Source] = field(compare=False, repr=False)
    # Tables built from each layer, kept while the layer is the same object.
    # Shared with the next Index, so an unchanged mod's tables are built once.
    _tables: dict[str, tuple[LayerIndex, Tables]] = field(
        default_factory=dict, compare=False, repr=False
    )

    def read(self, layer: str, path: str) -> bytes | None:
        """A file's bytes, read again from disk."""
        return read_file(self.sources[layer], path)

    def tables(self, layer: str) -> Tables:
        current = self.layers[layer]
        held = self._tables.get(layer)
        if held is not None and held[0] is current:
            return held[1]
        tables = _build_tables(current)
        self._tables[layer] = (current, tables)
        return tables


def _build_tables(layer: LayerIndex) -> Tables:
    paths: dict[str, str] = {}
    objects: dict[ObjectKey, list[tuple[str, Definition]]] = {}
    get = objects.get
    for path, entry in layer.files.items():
        folded = path.lower()
        if folded.partition("/")[0] not in _CONTENT:
            continue
        paths[folded] = path
        for definition in entry.definitions:
            key = definition.kind, definition.key
            found = get(key)
            if found is None:
                objects[key] = [(path, definition)]
            else:
                found.append((path, definition))
    return Tables(paths, objects)


@dataclass
class Indexer:
    """Index the game and every installed mod. Call it with a JobContext to run it.

    `previous` is the last index this window built. Layers that haven't changed
    are taken from it, without even loading their cache files.
    """

    library: Library
    cache_dir: Path
    previous: Index | None = None
    workers: int = field(default_factory=lambda: os.cpu_count() or 1)

    def __call__(self, ctx: JobContext) -> Index:
        ctx.progress(0, 0, "Looking for changed files")
        language = game_language(self.library.game.data_dir)
        settings = _settings(language)
        sources = [self._game_source(), *self._mod_sources()]

        layers: dict[str, LayerIndex] = {}
        todo: list[tuple[Source, int, dict[str, IndexedFile], list[str]]] = []
        for src in sources:
            ctx.check_cancelled()
            fingerprint = _fingerprint(src)
            old = self._old_layer(src.key, settings)
            if old is not None and old.fingerprint == fingerprint:
                layers[src.key] = old
                continue
            files, unread = _plan(src, old.files if old else {}, language)
            todo.append((src, fingerprint, files, unread))

        jobs = [
            (_slim(src, names), names)
            for src, _, _, unread in todo
            for names in _chunks(src, unread)
        ]
        total = sum(len(names) for _, names in jobs)
        results = self._read_all(ctx, jobs, todo, language, total)
        for src, fingerprint, files, _ in todo:
            files.update(results.get(src.key, {}))
            layer = LayerIndex(fingerprint, settings, files)
            layers[src.key] = layer
            save_msgpack(self._cache_file(src.key), layer)
        self._forget_removed(sources)

        tables = self.previous._tables if self.previous else {}
        return Index(language, layers, {src.key: src for src in sources}, tables)

    # Where layers come from

    def _game_source(self) -> Source:
        install = self.library.game.install_dir
        stamps: dict[str, Stamp] = {}
        for folder in GAME_FOLDERS:
            stamps.update(
                (f"{folder}/{name}", stamp) for name, stamp in _walk(install / folder).items()
            )
        return Source(GAME, install, None, stamps)

    def _mod_sources(self) -> Iterator[Source]:
        for mod in self.library.mods:
            if not mod.installed:
                continue
            stamps = self.library.files.get(mod.key, {})
            if mod.archive:
                yield Source(mod.key, None, Path(mod.archive), stamps)
            elif mod.root:
                yield Source(mod.key, Path(mod.root), None, stamps)

    def _old_layer(self, key: str, settings: int) -> LayerIndex | None:
        old = self.previous.layers.get(key) if self.previous else None
        if old is None:
            old = load_msgpack(self._cache_file(key), LayerIndex)
        return old if old is not None and old.settings == settings else None

    def _forget_removed(self, sources: list[Source]) -> None:
        """Delete the cache files of mods that are no longer installed."""
        wanted = {self._cache_file(src.key).name for src in sources}
        try:
            stale = [p for p in self.cache_dir.glob("*.msgpack") if p.name not in wanted]
        except OSError:
            return
        for path in stale:
            path.unlink(missing_ok=True)

    def _cache_file(self, key: str) -> Path:
        return self.cache_dir / f"{key.replace(':', '-').replace('/', '_')}.msgpack"

    # Reading

    def _read_all(
        self,
        ctx: JobContext,
        jobs: list[tuple[Source, list[str]]],
        todo: list[tuple[Source, int, dict[str, IndexedFile], list[str]]],
        language: str,
        total: int,
    ) -> dict[str, dict[str, IndexedFile]]:
        known = {src.key: files for src, _, files, _ in todo}
        results: dict[str, dict[str, IndexedFile]] = {}
        done = 0
        size = sum(_size(src, n) for src, names in jobs for n in names)

        def collect(src: Source, encoded: bytes) -> None:
            nonlocal done
            read = msgspec.msgpack.decode(encoded, type=dict[str, IndexedFile])
            done += len(read)
            for name, entry in read.items():
                old = known[src.key].get(name)
                # The same bytes under a new timestamp: keep the old definitions.
                if old is not None and old.hash == entry.hash and not entry.definitions:
                    entry = msgspec.structs.replace(entry, definitions=old.definitions)
                results.setdefault(src.key, {})[name] = entry
            ctx.progress(done, total, "Reading mod files for conflicts")

        if self.workers <= 1 or size < PARALLEL_BYTES:
            for src, names in jobs:
                ctx.check_cancelled()
                collect(src, read_chunk(src, names, language, _hashes(known[src.key], names)))
            return results

        with ProcessPoolExecutor(max_workers=self.workers) as pool:
            futures: dict[Future[bytes], Source] = {
                pool.submit(read_chunk, src, names, language, _hashes(known[src.key], names)): src
                for src, names in jobs
            }
            try:
                for future in as_completed(futures):
                    collect(futures[future], future.result())
            except BaseException:
                pool.shutdown(wait=False, cancel_futures=True)
                raise
        return results


def read_chunk(src: Source, names: list[str], language: str, hashes: dict[str, int]) -> bytes:
    """Read and parse some of a layer's files. Runs in a worker process.

    A file whose hash is in `hashes` is unchanged, so it isn't parsed: its entry
    comes back with no definitions, and the caller keeps the old ones. Returns
    msgpack, which crosses between processes much faster than pickled objects.
    """
    rules = load_rules()
    out: dict[str, IndexedFile] = {}
    with _Opened(src) as opened:
        for name in names:
            data = opened.read(name)
            stamp = _stamp(src, name)
            if data is None:
                out[name] = IndexedFile(stamp)
                continue
            digest = xxhash.xxh3_64_intdigest(data)
            if hashes.get(name) == digest:
                out[name] = IndexedFile(stamp, digest)
                continue
            definitions = read_definitions(name, data, rules.for_file(name), language)
            out[name] = IndexedFile(stamp, digest, definitions)
    return msgspec.msgpack.encode(out)


def read_file(src: Source, path: str) -> bytes | None:
    with _Opened(src) as opened:
        return opened.read(path)


class _Opened:
    def __init__(self, src: Source) -> None:
        self._root = src.root
        self._zip: zipfile.ZipFile | None = None
        if src.archive:
            try:
                self._zip = zipfile.ZipFile(src.archive)
            except OSError, zipfile.BadZipFile:
                self._zip = None

    def read(self, name: str) -> bytes | None:
        try:
            if self._zip is not None:
                return self._zip.read(name)
            if self._root is not None:
                return (self._root / name).read_bytes()
        except OSError, KeyError, zipfile.BadZipFile:
            pass
        return None

    def names(self) -> list[str]:
        if self._zip is None:
            return []
        return [i.filename for i in self._zip.infolist() if not i.is_dir()]

    def __enter__(self) -> _Opened:
        return self

    def __exit__(self, *_: object) -> None:
        if self._zip is not None:
            self._zip.close()


def _plan(
    src: Source, old: Mapping[str, IndexedFile], language: str
) -> tuple[dict[str, IndexedFile], list[str]]:
    """Every file in the layer, reusing old entries whose stamp is unchanged.

    Returns the entries known so far, and the files that must be read.
    """
    rules = load_rules()
    if src.archive:
        with _Opened(src) as opened:
            names = opened.names()
    else:
        names = list(src.stamps)
    files: dict[str, IndexedFile] = {}
    unread: list[str] = []
    for name in names:
        stamp = _stamp(src, name)
        entry = old.get(name)
        if not worth_reading(name, rules.for_file(name), language):
            files[name] = IndexedFile(stamp)
        elif entry is not None and entry.stamp == stamp and entry.hash:
            files[name] = entry
        else:
            if entry is not None:
                files[name] = entry  # kept until its new entry arrives, for its hash
            unread.append(name)
    return files, unread


def _slim(src: Source, names: list[str]) -> Source:
    """`src` with only the stamps a worker needs. A whole mod's would be sent
    to every worker with every chunk."""
    if src.archive:
        return Source(src.key, None, src.archive, {src.archive.name: _stamp(src, "")})
    return Source(src.key, src.root, None, {n: src.stamps[n] for n in names if n in src.stamps})


def _chunks(src: Source, names: list[str]) -> Iterator[list[str]]:
    chunk: list[str] = []
    size = 0
    for name in names:
        chunk.append(name)
        size += _size(src, name)
        if size >= CHUNK_BYTES:
            yield chunk
            chunk, size = [], 0
    if chunk:
        yield chunk


def _stamp(src: Source, name: str) -> Stamp:
    if src.archive:
        # A zip's members change only when the zip does. A Workshop folder
        # holding one zip lists the zip by name; a local zip mod lists only it.
        zip_stamp = src.stamps.get(src.archive.name)
        return zip_stamp or next(iter(src.stamps.values()), (0, 0))
    return src.stamps.get(name, (0, 0))


def _size(src: Source, name: str) -> int:
    # Members of a zip are all counted as the zip's size; close enough to split work.
    return _stamp(src, name)[0] if not src.archive else 1_000_000


def _hashes(files: Mapping[str, IndexedFile], names: list[str]) -> dict[str, int]:
    return {n: files[n].hash for n in names if n in files and files[n].hash}


def _fingerprint(src: Source) -> int:
    data = msgspec.msgpack.encode(
        [str(src.root or ""), str(src.archive or ""), sorted(src.stamps.items())]
    )
    return xxhash.xxh3_64_intdigest(data)


def _settings(language: str) -> int:
    rules = msgspec.msgpack.encode([INDEX_VERSION, language, repr(load_rules())])
    return xxhash.xxh3_64_intdigest(rules)
