"""Reading one mod: its descriptor, thumbnail and file list, using the cache.

A mod is re-read only when one of its files changed size or timestamp
(decision 9). Even then, its descriptor is only parsed again if the xxhash of
the descriptor files changed.
"""

import os
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import msgspec
import xxhash

from cold_steel.paradox.descriptor import Descriptor, decode_descriptor
from cold_steel.paradox.script import ParseError

type Stamp = tuple[int, int]  # size, modified time in nanoseconds

DESCRIPTOR = "descriptor.mod"
FALLBACK_PICTURE = "thumbnail.png"


class Mod(msgspec.Struct, frozen=True):
    key: str  # "workshop:<id>" or "local:<name of the .mod file>"
    source: Literal["workshop", "local"]
    name: str
    version: str = ""
    supported_version: str = ""
    tags: tuple[str, ...] = ()
    dependencies: tuple[str, ...] = ()  # names of mods this one loads after
    root: str = ""  # the mod's folder ("" for a local mod that is only a zip)
    archive: str = ""  # the zip, for mods shipped as one
    picture: str = ""  # the thumbnail file, or the zip that holds it
    picture_member: str = ""  # the thumbnail's name inside `picture` when that's a zip
    picture_stamp: str = ""  # changes whenever the thumbnail does
    remote_file_id: str = ""
    descriptor_file: str = ""  # the mod/*.mod file the game reads, if there is one
    problem: str = ""  # why the descriptor couldn't be read
    installed: bool = True  # False for a playset entry whose mod is gone


class CachedDescriptor(msgspec.Struct, frozen=True):
    stamp: Stamp
    hash: int
    descriptor: Descriptor
    problem: str = ""


class CachedMod(msgspec.Struct, frozen=True):
    files: dict[str, Stamp]  # every file in the mod, by path inside it
    outer_hash: int  # xxhash of the mod/*.mod file, 0 if there isn't one
    inputs_hash: int  # xxhash of both descriptor files together
    picture_name: str  # the thumbnail the descriptor asks for
    mod: Mod


@dataclass(frozen=True)
class ModSource:
    """Where to find one mod, worked out before reading it."""

    key: str
    source: Literal["workshop", "local"]
    root: Path | None
    archive: Path | None
    outer: Path | None  # the mod/*.mod file
    outer_entry: CachedDescriptor | None


def read_outer(path: Path, cached: CachedDescriptor | None) -> CachedDescriptor:
    """Read a mod/*.mod file, reusing the cached result if it hasn't changed."""
    st = path.stat()
    stamp = (st.st_size, st.st_mtime_ns)
    if cached and cached.stamp == stamp:
        return cached
    data = path.read_bytes()
    digest = xxhash.xxh3_64_intdigest(data)
    if cached and cached.hash == digest:
        return msgspec.structs.replace(cached, stamp=stamp)
    descriptor, problem = _decode(data, path.name)
    return CachedDescriptor(stamp=stamp, hash=digest, descriptor=descriptor, problem=problem)


def read_mod(src: ModSource, cached: CachedMod | None) -> CachedMod:
    """Read one mod, or return the cached copy if none of its files changed."""
    files = _walk(src.root) if src.root else _stamp_one(src.archive)
    outer = src.outer_entry
    outer_hash = outer.hash if outer else 0
    if cached and cached.files == files and cached.outer_hash == outer_hash:
        return cached

    archive = src.archive or _lone_zip(src.root, files)
    inner_bytes = _read_inner(src.root, archive, files)
    inputs_hash = xxhash.xxh3_64_intdigest(
        (inner_bytes or b"") + b"\0" + outer_hash.to_bytes(8, "little")
    )
    if cached and cached.inputs_hash == inputs_hash:
        # The descriptors are the same; only the thumbnail can have moved.
        mod = _with_picture(cached.mod, src.root, archive, files, cached.picture_name)
        return msgspec.structs.replace(cached, files=files, outer_hash=outer_hash, mod=mod)

    inner, inner_problem = _decode(inner_bytes, DESCRIPTOR) if inner_bytes else (Descriptor(), "")
    outer_desc = outer.descriptor if outer else Descriptor()
    # The game reads the mod/*.mod file; a Workshop folder's own descriptor.mod is
    # what its author wrote. Each side fills the other's gaps.
    desc = inner.fill_from(outer_desc) if src.source == "workshop" else outer_desc.fill_from(inner)
    problem = (outer.problem if outer else "") or inner_problem
    if not inner_bytes and not outer:
        problem = "No descriptor.mod"

    fallback_name = src.key.split(":", 1)[1]
    mod = Mod(
        key=src.key,
        source=src.source,
        name=desc.name or fallback_name,
        version=desc.version,
        supported_version=desc.supported_version,
        tags=desc.tags,
        dependencies=desc.dependencies,
        root=str(src.root or ""),
        archive=str(archive or ""),
        remote_file_id=desc.remote_file_id,
        descriptor_file=str(src.outer or ""),
        problem=problem,
    )
    return CachedMod(
        files=files,
        outer_hash=outer_hash,
        inputs_hash=inputs_hash,
        picture_name=desc.picture,
        mod=_with_picture(mod, src.root, archive, files, desc.picture),
    )


def read_picture(mod: Mod) -> bytes | None:
    """The thumbnail's bytes, from a file or from inside the mod's zip."""
    if not mod.picture:
        return None
    try:
        if mod.picture_member:
            with zipfile.ZipFile(mod.picture) as zf:
                return zf.read(mod.picture_member)
        return Path(mod.picture).read_bytes()
    except OSError, KeyError, zipfile.BadZipFile:
        return None


def _decode(data: bytes, label: str) -> tuple[Descriptor, str]:
    try:
        return decode_descriptor(data), ""
    except ParseError as error:
        return Descriptor(), f"{label} is broken ({error})"


def _with_picture(
    mod: Mod, root: Path | None, archive: Path | None, files: dict[str, Stamp], wanted: str
) -> Mod:
    """Point `mod.picture` at the thumbnail: in the folder first, then in the zip."""
    names = [n for n in (wanted, FALLBACK_PICTURE) if n]
    for name in names:
        rel = name.replace("\\", "/").lstrip("/")
        if root and rel in files:
            size, mtime = files[rel]
            return msgspec.structs.replace(
                mod, picture=str(root / rel), picture_member="", picture_stamp=f"{size}-{mtime}"
            )
    if archive:
        try:
            with zipfile.ZipFile(archive) as zf:
                members = set(zf.namelist())
        except OSError, zipfile.BadZipFile:
            members = set()
        for name in names:
            rel = name.replace("\\", "/").lstrip("/")
            if rel in members:
                st = archive.stat()
                return msgspec.structs.replace(
                    mod,
                    picture=str(archive),
                    picture_member=rel,
                    picture_stamp=f"{st.st_size}-{st.st_mtime_ns}",
                )
    return msgspec.structs.replace(mod, picture="", picture_member="", picture_stamp="")


def _read_inner(root: Path | None, archive: Path | None, files: dict[str, Stamp]) -> bytes | None:
    if root and DESCRIPTOR in files:
        try:
            return (root / DESCRIPTOR).read_bytes()
        except OSError:
            return None
    if archive:
        try:
            with zipfile.ZipFile(archive) as zf:
                return zf.read(DESCRIPTOR)
        except OSError, KeyError, zipfile.BadZipFile:
            return None
    return None


def _lone_zip(root: Path | None, files: dict[str, Stamp]) -> Path | None:
    """Some Workshop mods are just one zip and no descriptor.mod beside it."""
    if root is None or DESCRIPTOR in files:
        return None
    zips = [name for name in files if "/" not in name and name.lower().endswith(".zip")]
    return root / zips[0] if len(zips) == 1 else None


def _stamp_one(path: Path | None) -> dict[str, Stamp]:
    if path is None:
        return {}
    try:
        st = path.stat()
    except OSError:
        return {}
    return {path.name: (st.st_size, st.st_mtime_ns)}


def _walk(root: Path) -> dict[str, Stamp]:
    """Size and timestamp of every file under `root`, by path relative to it.

    Follows links to folders (a local mod is often a link), but never into a
    folder it has already seen, so a link loop can't trap it.
    """
    files: dict[str, Stamp] = {}
    seen: set[tuple[int, int]] = set()
    stack = [("", str(root))]
    while stack:
        prefix, folder = stack.pop()
        try:
            st = os.stat(folder)  # noqa: PTH116 (fast path; strings, not Paths)
            if (st.st_dev, st.st_ino) in seen:
                continue
            seen.add((st.st_dev, st.st_ino))
            entries = os.scandir(folder)
        except OSError:
            continue
        with entries:
            for entry in entries:
                try:
                    if entry.is_dir():
                        stack.append((f"{prefix}{entry.name}/", entry.path))
                    elif entry.is_file():
                        est = entry.stat()
                        files[prefix + entry.name] = (est.st_size, est.st_mtime_ns)
                except OSError:
                    continue
    return files
