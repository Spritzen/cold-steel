"""Build: a playset merged into one standalone mod.

    record = Builder(library, as_played(playset), index_cache, builds_dir())(ctx)

The build carries out what the playset and its patch mod say. It decides
nothing new: Phase 5 already settled the clashes the user cared about.

- Mods are copied in load order, the patch mod last. A file at the same path,
  ignoring case, replaces the earlier one, as it does in the game.
- Every other file keeps its name, so where two mods define one object, the
  game's file-name rules pick the same winner inside the one mod.
- Only files inside folders are copied. Loose files at the top of a mod
  (thumbnails, readmes, zips) aren't read by the game.
- The built mod gets the Cold Steel icon as its thumbnail, and every tag of
  the mods it's built from, each once.

Afterwards the Phase 4 winner rules run on the built mod: every clash in the
playset must have the same winner there (check_build).

The build lives in `~/.local/share/cold-steel/builds/<playset id>/`, linked into
the game's mod folder. Its record, `builds/<playset id>.json`, lists where
every file came from. A rebuild uses it to copy only the files whose source
changed. The build holds other authors' work, so it's for personal use only
(decision 49).
"""

import contextlib
import os
import shutil
import zipfile
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime
from importlib import resources
from pathlib import Path
from types import TracebackType

import msgspec

from cold_steel.core.conflicts import FILE, ConflictFinder, Found, rank
from cold_steel.core.deploy import (
    clone_file,
    deploy,
    in_the_way,
    link_or_clone,
    made_from,
    withdraw,
)
from cold_steel.core.deploy import not_ours as _not_ours
from cold_steel.core.index import GAME, Index, Indexer, LayerIndex, Source
from cold_steel.core.jobs import JobContext
from cold_steel.core.library import Library
from cold_steel.core.merge_rules import Rules, load_rules
from cold_steel.core.mods import DESCRIPTOR, FALLBACK_PICTURE, Stamp, _walk, is_pinned
from cold_steel.core.patch import supported_version
from cold_steel.paradox.descriptor import Descriptor, format_descriptor
from cold_steel.paradox.game import Game
from cold_steel.store import paths
from cold_steel.store.files import load_json, save_json
from cold_steel.store.playsets import Playset

BUILD_PREFIX = "cold_steel_build_"
BUILD_VERSION = 1
BUILT = "build"  # the built mod's layer, in check_build


class BuildError(Exception):
    pass


class BuiltFile(msgspec.Struct, frozen=True, array_like=True):
    layer: str  # the Mod.key it came from
    source: str  # its path inside that mod
    stamp: Stamp  # the source's size and timestamp when it was copied
    out: Stamp = (0, 0)  # the built file's, once copied
    replaced: tuple[str, ...] = ()  # mods whose file at this path it replaced


class Mismatch(msgspec.Struct, frozen=True):
    """A clash the built mod settles differently from the playset."""

    kind: str
    key: str
    expected: str  # the Mod.key whose version wins in the playset
    got: str  # the Mod.key whose version wins in the build; "" if it's missing


class BuildRecord(msgspec.Struct):
    version: int = BUILD_VERSION
    playset: str = ""  # the playset's id
    name: str = ""  # the playset's name when it was built
    built: str = ""  # when, as "2026-10-02 14:03"
    order: tuple[str, ...] = ()  # the mods, in load order
    names: dict[str, str] = msgspec.field(default_factory=dict)  # Mod.key -> name
    files: dict[str, BuiltFile] = msgspec.field(default_factory=dict)  # by path in the build
    left_out: list[tuple[str, str]] = msgspec.field(default_factory=list)  # Mod.key, path
    mismatches: list[Mismatch] = msgspec.field(default_factory=list)
    copied: int = 0  # files the last build copied; the rest were already there
    built_playset: str = ""  # the id of the playset that plays the built mod


@dataclass(frozen=True)
class BuildPlan:
    order: tuple[str, ...]
    files: dict[str, BuiltFile]  # by path in the build; `out` not filled in yet
    left_out: tuple[tuple[str, str], ...]


def builds_dir() -> Path:
    return paths.data_dir() / "builds"


def build_name(playset: Playset) -> str:
    return f"Cold Steel build: {playset.name}"


def build_key(playset_id: str) -> str:
    """The built mod's Mod.key, once a scan has found it."""
    return f"local:{BUILD_PREFIX}{playset_id}"


def built_from(key: str) -> str | None:
    """The playset id a built mod was built from, or None if `key` isn't a build's.
    Ids are plain names, never paths: a mod called `cold_steel_build_..` isn't ours."""
    return made_from(key, BUILD_PREFIX)


def load_record(root: Path, playset_id: str) -> BuildRecord | None:
    return load_json(root / f"{playset_id}.json", BuildRecord)


def save_record(root: Path, record: BuildRecord) -> None:
    save_json(root / f"{record.playset}.json", record)


# Planning


def plan_build(order: Sequence[str], index: Index) -> BuildPlan:
    """Where every file of the build comes from. `order` is the mods in load order."""
    files: dict[str, BuiltFile] = {}
    by_folded: dict[str, str] = {}  # lowercased path -> path in the build
    folders: dict[str, str] = {}  # lowercased folder -> its spelling in the build
    left_out: list[tuple[str, str]] = []
    for layer in order:
        for path, entry in index.layers[layer].files.items():
            folder, _, name = path.rpartition("/")
            if not folder or any(part.startswith(".") for part in path.split("/")):
                left_out.append((layer, path))
                continue
            out = f"{_spelling(folder, folders)}/{name}"
            replaced: tuple[str, ...] = ()
            earlier = by_folded.pop(out.lower(), None)
            if earlier is not None:
                gone = files.pop(earlier)
                replaced = (*gone.replaced, gone.layer)
            by_folded[out.lower()] = out
            files[out] = BuiltFile(layer, path, entry.stamp, replaced=replaced)
    return BuildPlan(tuple(order), files, tuple(left_out))


def _spelling(folder: str, seen: dict[str, str]) -> str:
    """The folder as first spelled in the build. "Common/x" from one mod and
    "common/x" from another become one folder, as the game ignores case."""
    spelled = ""
    for part in folder.split("/"):
        candidate = f"{spelled}/{part}" if spelled else part
        spelled = seen.setdefault(candidate.lower(), candidate)
    return spelled


# Writing


def write_files(
    plan: BuildPlan, previous: BuildRecord | None, folder: Path, index: Index, ctx: JobContext
) -> tuple[dict[str, BuiltFile], int]:
    """Bring the build's folder in line with the plan. Returns every file, with
    its built stamp, and how many were copied.

    A file is copied again only if its source changed, it came from another
    mod, or the built file was changed by hand. Files from a pinned copy are
    hard links to the snapshot store, so they take no space.
    """
    folder.mkdir(parents=True, exist_ok=True)
    existing = _walk(folder)
    old = previous.files if previous else {}
    by_layer: dict[str, list[str]] = {}
    for out, file in plan.files.items():
        by_layer.setdefault(file.layer, []).append(out)

    written: dict[str, BuiltFile] = {}
    copied = done = 0
    for layer in plan.order:
        with _Reader(index.sources[layer]) as reader:
            for out in by_layer.get(layer, ()):
                if done % 500 == 0:
                    ctx.progress(done, len(plan.files), "Building the mod")
                done += 1
                want = plan.files[out]
                had = old.get(out)
                if (
                    had is not None
                    and (had.layer, had.source, had.stamp) == (want.layer, want.source, want.stamp)
                    and existing.get(out) == had.out
                ):
                    written[out] = msgspec.structs.replace(want, out=had.out)
                    continue
                target = folder / out
                target.parent.mkdir(parents=True, exist_ok=True)
                target.unlink(missing_ok=True)
                reader.copy(want.source, target, linkable=is_pinned(layer))
                st = target.stat()
                written[out] = msgspec.structs.replace(want, out=(st.st_size, st.st_mtime_ns))
                copied += 1

    for path in existing.keys() - plan.files.keys() - {DESCRIPTOR, FALLBACK_PICTURE}:
        (folder / path).unlink(missing_ok=True)
    _remove_empty_folders(folder)
    return written, copied


class _Reader:
    """One mod's files, from its folder or its zip, copied out one at a time."""

    def __init__(self, src: Source) -> None:
        self._root = src.root
        self._zip = zipfile.ZipFile(src.archive) if src.archive else None

    def copy(self, name: str, target: Path, *, linkable: bool) -> None:
        if self._zip is not None:
            target.write_bytes(self._zip.read(name))
        elif self._root is not None:
            (link_or_clone if linkable else clone_file)(self._root / name, target)

    def __enter__(self) -> _Reader:
        return self

    def __exit__(
        self,
        kind: type[BaseException] | None,
        error: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        if self._zip is not None:
            self._zip.close()


def merged_tags(tag_lists: Iterable[Sequence[str]]) -> tuple[str, ...]:
    """Every tag once, in the order first seen. "Balance" and "balance" are one tag."""
    seen: dict[str, str] = {}
    for tags in tag_lists:
        for tag in tags:
            seen.setdefault(tag.strip().lower(), tag.strip())
    return tuple(t for t in seen.values() if t)


def _write_thumbnail(target: Path) -> None:
    """The Cold Steel icon, rendered from data/cold-steel.svg. Written only if it changed."""
    data = resources.files("cold_steel").joinpath("data/thumbnail.png").read_bytes()
    if not target.exists() or target.read_bytes() != data:
        target.write_bytes(data)


def _remove_empty_folders(root: Path) -> None:
    for folder, _, _ in sorted(os.walk(root), key=lambda w: -len(w[0])):
        if Path(folder) != root:
            with contextlib.suppress(OSError):
                Path(folder).rmdir()  # only succeeds when empty


# Checking


def check_build(found: Found, plan: BuildPlan, index: Index, rules: Rules) -> list[Mismatch]:
    """Every clash in the playset whose winner differs in the built mod.

    The built mod is indexed from its sources' entries, so nothing is parsed
    again. Then each clash is settled by the same rules the conflict finder
    uses, with the game and the build as the only two layers.
    """
    files = {out: index.layers[f.layer].files[f.source] for out, f in plan.files.items()}
    layers = {BUILT: LayerIndex(0, 0, files)}
    if GAME in index.layers:
        layers = {GAME: index.layers[GAME], **layers}
    built = Index(index.language, layers, {})
    order = tuple(layers)
    game_paths = built.tables(GAME).paths if GAME in layers else {}
    replaced = frozenset(
        (GAME, game_paths[folded]) for folded in built.tables(BUILT).paths if folded in game_paths
    )
    merged = Found(order, (), built, replaced)
    position = {layer: n for n, layer in enumerate(order)}

    def source(layer: str, path: str) -> str:
        return plan.files[path].layer if layer == BUILT else layer

    mismatches: list[Mismatch] = []
    for conflict in found.conflicts:
        expected = conflict.winning
        claims = merged.claims(conflict.kind, conflict.key)
        if not claims:
            mismatches.append(Mismatch(conflict.kind, conflict.key, expected.layer, ""))
            continue
        if conflict.kind == FILE:
            got = claims[-1]
            same = got.layer == BUILT and (
                (plan.files[got.path].layer, plan.files[got.path].source)
                == (expected.layer, expected.path)
            )
        else:
            rule = conflict.rule or rules.for_file(claims[0].path)
            ordered, winner, _ = rank(claims, rule, position)
            got = ordered[winner]
            assert got.definition is not None and expected.definition is not None
            same = got.definition.digest == expected.definition.digest
        if not same:
            mismatches.append(
                Mismatch(conflict.kind, conflict.key, expected.layer, source(got.layer, got.path))
            )
    return mismatches


# The whole job


@dataclass
class Builder:
    """Build a playset into one mod. Call it with a JobContext to run it.

    `playset` is the playset as played (playsets.as_played), with its patch
    mod. `previous` is the last index this window built, as for Indexer.
    """

    library: Library
    playset: Playset
    index_cache: Path
    root: Path
    previous: Index | None = None
    rules: Rules | None = None

    def __call__(self, ctx: JobContext) -> tuple[BuildRecord, Index]:
        game = self.library.game
        name = f"{BUILD_PREFIX}{self.playset.id}"
        if why := in_the_way(game, name):
            raise BuildError(why)
        rules = self.rules or load_rules()
        index = Indexer(self.library, self.index_cache, self.previous)(ctx)
        # The build itself is left out, should it be in the playset.
        mine = frozenset(k for k in index.layers if k.startswith(f"local:{BUILD_PREFIX}"))
        found = ConflictFinder(index, self.playset, self.library, rules, leave_out=mine)(ctx)
        ctx.progress(0, 0, "Planning the build")
        plan = plan_build([k for k in found.order if k != GAME], index)

        previous = load_record(self.root, self.playset.id)
        # Forgotten while files change, so a build that stops halfway is redone in full.
        (self.root / f"{self.playset.id}.json").unlink(missing_ok=True)
        folder = self.root / self.playset.id
        files, copied = write_files(plan, previous, folder, index, ctx)
        mods = {m.key: m for m in self.library.every_mod}
        descriptor = Descriptor(
            name=build_name(self.playset),
            version=datetime.now().strftime("%Y.%m.%d"),
            supported_version=supported_version(game.version),
            tags=merged_tags(mods[k].tags for k in plan.order if k in mods),
            picture=FALLBACK_PICTURE,
        )
        (folder / DESCRIPTOR).write_text(format_descriptor(descriptor), "utf-8")
        _write_thumbnail(folder / FALLBACK_PICTURE)

        ctx.progress(0, 0, "Checking the build against the playset")
        mismatches = check_build(found, plan, index, rules)
        record = BuildRecord(
            playset=self.playset.id,
            name=self.playset.name,
            built=datetime.now().strftime("%Y-%m-%d %H:%M"),
            order=plan.order,
            names={k: mods[k].name if k in mods else k for k in plan.order},
            files=files,
            left_out=list(plan.left_out),
            mismatches=mismatches,
            copied=copied,
            built_playset=previous.built_playset if previous else "",
        )
        deploy(folder, name, descriptor, game)
        save_record(self.root, record)
        return record, index


def not_ours(playset_id: str, root: Path, game: Game) -> str | None:
    """Why the mod folder's `cold_steel_build_<id>` isn't a build of ours, or
    None if it is (or if nothing is there). Check before remove_build()."""
    return _not_ours(game, f"{BUILD_PREFIX}{playset_id}", root / playset_id)


def remove_build(playset_id: str, root: Path, game: Game | None) -> None:
    """Delete a playset's build, its record, and its link in the mod folder."""
    if game is not None:
        withdraw(game, f"{BUILD_PREFIX}{playset_id}")
    shutil.rmtree(root / playset_id, ignore_errors=True)
    (root / f"{playset_id}.json").unlink(missing_ok=True)
