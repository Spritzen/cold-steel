"""Deleting a local mod: which of its files go, and which stay.

    files = local_files(mod, game.mod_dir)

A local mod is its mod/*.mod file plus the folder or zip that file points at.
Only things directly inside the mod folder are deleted. A folder somewhere
else (your own project, say) stays, and so does whatever a link points at:
only the link goes. Cold Steel's own mods (patch, build, pinned copies) are
never offered: they have their own ways to go.
"""

import os
from dataclasses import dataclass
from pathlib import Path

from cold_steel.core.mods import Mod, is_pinned

OURS = "cold_steel_"  # the start of every mod folder name Cold Steel writes


@dataclass(frozen=True)
class LocalFiles:
    """What deleting one local mod removes, and what it leaves."""

    descriptor: Path | None  # the mod/*.mod file
    content: Path | None  # the folder or zip, when it's inside the mod folder
    link: bool  # `content` is a link: only the link goes, never what it points at
    kept: Path | None  # the folder or zip outside the mod folder, left alone

    @property
    def paths(self) -> tuple[Path, ...]:
        return tuple(p for p in (self.descriptor, self.content) if p is not None)


def local_files(mod: Mod, mod_dir: Path) -> LocalFiles | None:
    """The files deleting `mod` removes, or None if it can't be deleted here."""
    if mod.source != "local" or not mod.installed or is_pinned(mod.key):
        return None
    if mod.key.removeprefix("local:").startswith(OURS):
        return None
    mod_dir = Path(os.path.normpath(mod_dir))

    def inside(value: str) -> Path | None:
        path = Path(os.path.normpath(value)) if value else None
        return path if path is not None and path.parent == mod_dir else None

    descriptor = inside(mod.descriptor_file)
    target = mod.root or mod.archive
    content = inside(target)
    if content is not None and content.name.startswith(OURS):
        return None
    if descriptor is None and content is None:
        return None
    kept = Path(target) if target and content is None else None
    link = content is not None and content.is_symlink()
    return LocalFiles(descriptor, content, link, kept)
