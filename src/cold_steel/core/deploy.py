"""Putting a mod we made into the game's mod folder, and copying files fast.

    if why := in_the_way(game, "cold_steel_build_x"): ...
    deploy(folder, "cold_steel_build_x", descriptor, game)

The mod folder gets a link to the mod's folder, not a copy: a copy went stale
in STG and nobody noticed. A rebuild is live at once. The .mod file points at
the link by its full path. That path is the same on the host as in the dev
container (decision 5), so the game reads it as written.
"""

import contextlib
import errno
import fcntl
import os
import re
import shutil
from pathlib import Path

import msgspec

from cold_steel.paradox.descriptor import Descriptor, format_descriptor
from cold_steel.paradox.game import Game

# The Linux ioctl that makes `dst` share `src`'s data (a reflink).
FICLONE = 0x40049409
_PLAIN_ID = re.compile(r"[A-Za-z0-9_-]+")  # uuid4s, as playsets.new_id() makes


def made_from(key: str, prefix: str) -> str | None:
    """The playset id in a Mod.key of ours, `local:<prefix><id>`, or None.
    Ids are plain names, never paths: a mod called `<prefix>..` isn't ours."""
    playset_id = key.removeprefix(f"local:{prefix}")
    return playset_id if key != playset_id and _PLAIN_ID.fullmatch(playset_id) else None


def in_the_way(game: Game, name: str) -> str | None:
    """Why `name` can't be linked into the mod folder, or None if it can."""
    link = game.mod_dir / name
    if link.exists() and not link.is_symlink():
        return f"{link} is in the way. It isn't ours, so move it, then try again."
    return None


def not_ours(game: Game, name: str, folder: Path) -> str | None:
    """Why the mod folder's `name` isn't our link to `folder`, or None if it
    is (or if nothing is there). Check before withdrawing it."""
    link = game.mod_dir / name
    if link.is_symlink() and link.readlink() != folder:
        return f"{link} links somewhere Cold Steel didn't put it, so it isn't ours to delete."
    return in_the_way(game, name)


def deploy(folder: Path, name: str, descriptor: Descriptor, game: Game) -> Path:
    """Link `folder` into the game's mod folder as `name`, with `name`.mod
    beside it. Returns the .mod file. Check in_the_way() first."""
    link = game.mod_dir / name
    outer = link.with_name(name + ".mod")
    game.mod_dir.mkdir(parents=True, exist_ok=True)
    if not (link.is_symlink() and link.readlink() == folder):
        link.unlink(missing_ok=True)
        link.symlink_to(folder, target_is_directory=True)
    write_outer(outer, msgspec.structs.replace(descriptor, path=str(link)))
    return outer


def withdraw(game: Game, name: str) -> None:
    """Remove a link and its .mod file from the mod folder."""
    link = game.mod_dir / name
    if link.is_symlink():
        link.unlink()
    link.with_name(name + ".mod").unlink(missing_ok=True)


def write_outer(outer: Path, descriptor: Descriptor) -> None:
    """Write a mod/*.mod file, only if its text changed."""
    text = format_descriptor(descriptor)
    if not outer.exists() or outer.read_text("utf-8") != text:
        outer.parent.mkdir(parents=True, exist_ok=True)
        outer.write_text(text, "utf-8")


def clone_file(src: Path, dst: Path) -> None:
    """Copy a file as a reflink where the disk supports it (btrfs, XFS): no
    extra space, and still a separate file, so changing one never changes the
    other. Elsewhere it's a real copy."""
    with src.open("rb") as source, dst.open("wb") as target:
        try:
            fcntl.ioctl(target.fileno(), FICLONE, source.fileno())
            return
        except OSError:
            pass
    shutil.copyfile(src, dst)


def link_or_clone(src: Path, dst: Path) -> None:
    """Hard-link `dst` to `src`, or clone it where a hard link isn't possible.
    Only for files that are never changed in place: ours, in the snapshot store."""
    try:
        os.link(src, dst)
    except OSError as error:
        if error.errno not in (errno.EXDEV, errno.EMLINK, errno.EPERM, errno.ENOTSUP):
            raise
        with contextlib.suppress(FileNotFoundError):
            dst.unlink()
        clone_file(src, dst)
