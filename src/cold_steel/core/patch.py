"""The patch mod: a playset's conflict choices, written as a mod that loads last.

    plan = plan_patch(found, book.resolutions, index)
    folder = write_patch(plan, playset, game, patches_dir())

It's always rebuilt whole from the saved choices and never edited by hand, so
it can't drift from them (STG's rule). How each choice is made to win, under
the winner rules in merge_rules.py:

- **A whole file:** the patch ships the chosen file at the same path. The
  patch loads last, so its file replaces the others.
- **An object where the last definition wins:** a file holding just that
  object, named to sort after every file that defines it.
- **An object where the first definition wins:** the same, named to sort first.
- **Localisation:** one file in a replace/ folder, which beats the rest.

The patch lives in our data folder. The game's mod folder gets a link to it
and a .mod file, so a rebuild is live at once.
"""

import re
import shutil
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

import msgspec
import xxhash

from cold_steel.core.conflicts import FILE, Claim, Conflict, Found
from cold_steel.core.definitions import read_definitions
from cold_steel.core.health import BOM, SCRIPT_SUFFIXES, Issue, check_localisation, check_script
from cold_steel.core.index import Index
from cold_steel.core.resolve import choices_digest, chosen_claim, is_current
from cold_steel.paradox.descriptor import Descriptor, format_descriptor
from cold_steel.paradox.game import Game
from cold_steel.paradox.script import scan
from cold_steel.store import paths
from cold_steel.store.playsets import Playset, PlaysetEntry
from cold_steel.store.resolutions import Resolution

PATCH_PREFIX = "cold_steel_patch_"
_UNSAFE = re.compile(r"[^A-Za-z0-9_.-]+")
_VERSION = re.compile(r"v?(\d+)\.(\d+)")


class PatchError(Exception):
    pass


def patch_key(playset_id: str) -> str:
    """The patch mod's Mod.key, once a scan has found it."""
    return f"local:{PATCH_PREFIX}{playset_id}"


def patch_name(playset: Playset) -> str:
    return f"Cold Steel patch: {playset.name}"


def patches_dir() -> Path:
    return paths.data_dir() / "patches"


@dataclass(frozen=True)
class LeftOut:
    resolution: Resolution
    why: str


@dataclass(frozen=True)
class PatchPlan:
    files: dict[str, bytes]  # path inside the mod -> contents
    written: tuple[Resolution, ...]
    left_out: tuple[LeftOut, ...]
    digest: int  # choices_digest of the choices it was made from


class _Skip(Exception):  # noqa: N818 (a reason to leave a choice out, not an error)
    pass


def plan_patch(found: Found, resolutions: tuple[Resolution, ...], index: Index) -> PatchPlan:
    """The files the patch mod needs. `found` must leave the patch itself out."""
    conflicts = {(c.kind, c.key): c for c in found.conflicts}
    files: dict[str, bytes] = {}
    written: list[Resolution] = []
    left_out: list[LeftOut] = []
    localisation: list[tuple[Resolution, Conflict, bytes]] = []
    for resolution in resolutions:
        conflict = conflicts.get((resolution.kind, resolution.key))
        try:
            if conflict is None:
                raise _Skip("It isn't a conflict in this playset any more.")
            if not is_current(resolution, conflict, index):
                raise _Skip("A version changed after you chose. It needs another look.")
            body = _body(resolution, conflict, index)
            if conflict.kind == FILE:
                files[conflict.key] = body
            elif conflict.rule is not None and conflict.rule.unit == "localisation":
                localisation.append((resolution, conflict, body))
                continue  # written below, all in one file
            else:
                path, data = object_file(conflict, body, index, chosen_claim(resolution, conflict))
                if path in files:  # two keys that only differ in odd characters
                    stem, dot, suffix = path.rpartition(".")
                    path = f"{stem}_{_short_hash(conflict.kind + conflict.key)}{dot}{suffix}"
                files[path] = data
        except _Skip as why:
            left_out.append(LeftOut(resolution, str(why)))
            continue
        written.append(resolution)
    if localisation:
        added = _localisation_files([(c, b) for _, c, b in localisation], index.language)
        if added is None:
            reason = "No file name sorts after the other replace/ files that define it."
            left_out += (LeftOut(r, reason) for r, _, _ in localisation)
        else:
            files.update(added)
            written += (r for r, _, _ in localisation)
    return PatchPlan(files, tuple(written), tuple(left_out), choices_digest(resolutions))


def _body(resolution: Resolution, conflict: Conflict, index: Index) -> bytes:
    """The chosen version's bytes: a whole file, or just the object."""
    if resolution.own:
        return own_bytes(conflict, resolution.text)
    claim = chosen_claim(resolution, conflict)
    if claim is None:
        raise _Skip("The version you chose isn't in this playset any more.")
    data = index.read(claim.layer, claim.path)
    if data is None:
        raise _Skip(f"{claim.path} can't be read any more. Rescan, then try again.")
    if claim.definition is None:
        return data
    return data[claim.definition.start : claim.definition.end]


def own_bytes(conflict: Conflict, text: str) -> bytes:
    """The user's own version, as the patch stores it."""
    data = text.encode("utf-8")
    if conflict.kind == FILE and conflict.key.lower().endswith(".yml"):
        return data if data.startswith(BOM) else BOM + data
    if conflict.kind != FILE:
        data = data.strip()
    return data


# One object's file


def object_file(
    conflict: Conflict, body: bytes, index: Index, chosen: Claim | None
) -> tuple[str, bytes]:
    """The path and bytes of a patch file holding one object, named so it wins.

    Raises _Skip if no name can sort ahead of the other files.
    """
    rule = conflict.rule
    assert rule is not None
    # The chosen version's file, or the winner's for the user's own: it gives
    # the folder, the suffix, and any block the object sits inside.
    like = chosen or conflict.winning
    folder, _, like_name = like.path.rpartition("/")
    suffix = "." + like_name.rpartition(".")[2].lower() if "." in like_name else ".txt"
    rivals = [c.path.rpartition("/")[2] for c in conflict.claims]
    stem = "cold_steel_" + _UNSAFE.sub("_", conflict.key)[:80]
    name = winning_name(rivals, stem + suffix, first=rule.first_wins)
    if name is None:
        raise _Skip("No file name sorts ahead of the others that define it.")

    text = body.strip() + b"\n"
    if rule.unit == "define":
        group = conflict.key.partition(".")[0].encode()
        text = group + b" = {\n\t" + text + b"}\n"
    elif rule.unit == "name" and (wrapper := _wrapper(index, like)):
        text = wrapper + b" = {\n" + text + b"}\n"
    if folder.lower().startswith("events") and "." in conflict.key:
        # Event ids need their namespace declared in the same file.
        namespace = conflict.key.rpartition(".")[0].encode()
        text = b"namespace = " + namespace + b"\n\n" + text
    return f"{folder}/{name}", text


def winning_name(rivals: Iterable[str], tail: str, *, first: bool) -> str | None:
    """A file name that sorts before (`first`) or after every rival, with and
    without case, or None if none can. "!" sorts before letters and digits.
    "z" sorts after them, and "~" after "z": some mods start a name with "~" to
    load last, so "z" is tried first and "~" when it isn't enough.
    """
    rivals = list(rivals)
    for char in ("!",) if first else ("z", "~"):
        run = max((len(r) - len(r.lstrip(char + char.upper())) for r in rivals), default=0)
        name = char * max(2, run + 1) + "_" + tail
        if all(_sorts(name, rival, first=first) for rival in rivals):
            return name
    return None


def _sorts(name: str, rival: str, *, first: bool) -> bool:
    pairs = ((name, rival), (name.casefold(), rival.casefold()))
    return all((a < b) if first else (a > b) for a, b in pairs)


def _wrapper(index: Index, claim: Claim) -> bytes | None:
    """The key of the block the object sits inside, like spriteTypes, if any."""
    data = index.read(claim.layer, claim.path)
    d = claim.definition
    if data is None or d is None:
        return None
    for entry in scan(data):
        if entry.block and entry.start < d.start and d.end <= entry.end:
            return entry.key
    return None


def _localisation_files(
    chosen: list[tuple[Conflict, bytes]], language: str
) -> dict[str, bytes] | None:
    """One file per localisation folder, in its replace/ folder. None if no
    name sorts after every other replace/ file."""
    by_folder: dict[str, list[bytes]] = {}
    rivals: list[str] = []
    for conflict, body in chosen:
        top = conflict.winning.path.partition("/")[0]
        by_folder.setdefault(top, []).append(body.strip())
        rivals += (c.path.rpartition("/")[2] for c in conflict.claims if _replaces(c.path))
    # Among replace/ files the last name is assumed to win (decision 42).
    name = winning_name(rivals, f"cold_steel_{language}.yml", first=False)
    if name is None:
        return None
    return {
        f"{top}/replace/{name}": localisation_text(lines, language)
        for top, lines in by_folder.items()
    }


def localisation_text(lines: list[bytes], language: str) -> bytes:
    body = b"".join(b" " + line + b"\n" for line in lines)
    return BOM + language.encode() + b":\n" + body


def _replaces(path: str) -> bool:
    return "/replace/" in path.lower()


# Checking the user's own version


def check_own(conflict: Conflict, text: str, index: Index) -> list[str]:
    """What's wrong with the user's own version, in plain words. Empty if nothing.

    Runs the health checks on it, then makes sure the patch file it would go in
    still defines the object, so it really replaces the others.
    """
    data = own_bytes(conflict, text)
    if conflict.kind == FILE:
        name = conflict.key.rpartition("/")[2]
        lowered = name.lower()
        if lowered.endswith(".yml"):
            return [_said(i) for i in check_localisation(name, data)]
        if lowered.endswith(SCRIPT_SUFFIXES):
            return [_said(i) for i in check_script(name, data)]
        return []
    rule = conflict.rule
    assert rule is not None
    if rule.unit == "localisation":
        path = f"localisation/replace/cold_steel_{index.language}.yml"
        file = localisation_text([data], index.language)
        # The language line comes first, so the user's lines start at 2.
        problems = [_said(i, -1) for i in check_localisation(path, file)]
    else:
        problems = [_said(i) for i in check_script("", data)]
        try:
            path, file = object_file(conflict, data, index, None)
        except _Skip as why:
            return [*problems, str(why)]
    if problems:
        return problems
    defined = {(d.kind, d.key) for d in read_definitions(path, file, rule, index.language)}
    if (conflict.kind, conflict.key) not in defined:
        return [
            f"This doesn't define {conflict.key} any more, so it wouldn't replace the "
            "other versions. Keep the object's name as it was."
        ]
    return []


def _said(issue: Issue, shift: int = 0) -> str:
    return f"Line {issue.line + shift}: {issue.text}" if issue.line else issue.text


# Writing it


def write_patch(plan: PatchPlan, playset: Playset, game: Game, root: Path) -> Path:
    """Rebuild the patch mod in `root`, then link it into the game's mod folder.

    Returns the patch's folder. Raises PatchError, having written nothing, if
    something that isn't ours is in the way.
    """
    folder = root / playset.id
    link = game.mod_dir / f"{PATCH_PREFIX}{playset.id}"
    outer = link.with_name(link.name + ".mod")
    if link.exists() and not link.is_symlink():
        raise PatchError(f"{link} is in the way. Move it, then generate the patch again.")

    descriptor = Descriptor(
        name=patch_name(playset), version="1", supported_version=_supported(game.version)
    )
    new = root / f".{playset.id}.new"
    old = root / f".{playset.id}.old"
    shutil.rmtree(new, ignore_errors=True)
    for path, data in plan.files.items():
        target = new / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    new.mkdir(parents=True, exist_ok=True)
    (new / "descriptor.mod").write_text(format_descriptor(descriptor), "utf-8")
    shutil.rmtree(old, ignore_errors=True)
    if folder.exists():
        folder.rename(old)
    new.rename(folder)
    shutil.rmtree(old, ignore_errors=True)

    game.mod_dir.mkdir(parents=True, exist_ok=True)
    if not (link.is_symlink() and link.readlink() == folder):
        link.unlink(missing_ok=True)
        link.symlink_to(folder, target_is_directory=True)
    text = format_descriptor(msgspec.structs.replace(descriptor, path=str(link)))
    if not outer.exists() or outer.read_text("utf-8") != text:
        outer.write_text(text, "utf-8")
    return folder


def remove_patch(playset_id: str, game: Game, root: Path) -> None:
    """Delete a playset's patch mod, with its link and .mod file."""
    link = game.mod_dir / f"{PATCH_PREFIX}{playset_id}"
    if link.is_symlink():
        link.unlink()
    link.with_name(link.name + ".mod").unlink(missing_ok=True)
    shutil.rmtree(root / playset_id, ignore_errors=True)


def with_patch_last(playset: Playset) -> Playset:
    """The playset with its patch mod at the end, turned on."""
    key = patch_key(playset.id)
    entries = tuple(e for e in playset.entries if e.key != key)
    return msgspec.structs.replace(
        playset, entries=(*entries, PlaysetEntry(key, True, patch_name(playset)))
    )


def _supported(version: str) -> str:
    found = _VERSION.match(version)
    return f"v{found.group(1)}.{found.group(2)}.*" if found else ""


def _short_hash(text: str) -> str:
    return f"{xxhash.xxh3_64_intdigest(text.encode()):016x}"[:8]
