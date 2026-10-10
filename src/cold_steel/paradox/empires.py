"""The game's empire file: every empire designed in its empire creator. See
docs/reference/stellaris-files.md.

    "Divine Elven Order"=
    {
        key="Divine Elven Order"
        species= { class="HUM" trait="trait_venerable" … }
        planet_class="pc_pd_cold_superhabitable"
        …
    }

The file is split into its top-level blocks, and each block's bytes are kept
exactly as written. Writing joins kept bytes, so an empire the game wrote is
never written out again from what we parsed. Reads are cached by the file's
size and timestamp (decision 9).
"""

import re
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

import msgspec

from cold_steel.paradox.backup import backup_file
from cold_steel.paradox.script import Node, ParseError, parse, scan

EMPIRE_GLOB = "user_empire_designs_v*.txt"
_VERSION = re.compile(r"user_empire_designs_v([\d.]+)\.txt$")

type Use = tuple[str, str]  # (folder that defines it, its key): ("common/traits", "trait_x")


class EmpireInfo(msgspec.Struct, frozen=True):
    """What an empire's block says, for showing it and checking its mods."""

    species: str = ""  # its species' name: "Elf"
    authority: str = ""  # "auth_imperial"
    government: str = ""
    origin: str = ""
    ethics: tuple[str, ...] = ()
    civics: tuple[str, ...] = ()
    # Everything it uses that a mod can add, by the folder that defines it.
    uses: tuple[Use, ...] = ()


@dataclass(frozen=True)
class Empire:
    name: str  # its key in the file: the name you gave it
    text: bytes  # its block, exactly as in the file, with the line break after it
    info: EmpireInfo | None  # None when the block couldn't be read
    problem: str = ""  # why it couldn't be read


@dataclass(frozen=True)
class EmpireFile:
    path: Path
    head: bytes  # whatever is before the first block
    empires: tuple[Empire, ...]  # in file order

    @property
    def names(self) -> tuple[str, ...]:
        return tuple(e.name for e in self.empires)

    def get(self, name: str) -> Empire | None:
        return next((e for e in self.empires if e.name == name), None)


def empire_file(data_dir: Path) -> Path | None:
    """The empire file the game uses: the newest `user_empire_designs_v*.txt`
    by its format version. None if there's none yet."""
    found: list[tuple[tuple[int, ...], Path]] = []
    for path in data_dir.glob(EMPIRE_GLOB):
        match = _VERSION.search(path.name)
        if match and path.is_file():
            parts = tuple(int(p) for p in match.group(1).split(".") if p.isdigit())
            found.append((parts, path))
    return max(found)[1] if found else None


def split_empires(data: bytes) -> tuple[bytes, list[tuple[str, bytes]]]:
    """The bytes before the first block, and each top-level block with the
    gap after it, keyed by its name. Joined back in order, they're `data`."""
    entries = scan(data)
    if not entries:
        return data, []
    head = data[: entries[0].start]
    blocks: list[tuple[str, bytes]] = []
    for entry, after in zip(entries, [*entries[1:], None], strict=True):
        end = after.start if after is not None else len(data)
        blocks.append((_text(entry.key), data[entry.start : end]))
    return head, blocks


def parse_empires(path: Path, data: bytes) -> EmpireFile:
    head, blocks = split_empires(data)
    return EmpireFile(path, head, tuple(_empire(name, text) for name, text in blocks))


def read_empire_file(path: Path) -> EmpireFile:
    """Raises OSError if it can't be read."""
    return parse_empires(path, path.read_bytes())


def join_empires(head: bytes, texts: Iterable[bytes]) -> bytes:
    """A file from kept blocks. A block without a line break after it, as the
    last one may be, gets one before the next."""
    out = bytearray(head)
    newline = b"\r\n" if b"\r\n" in head else b"\n"
    for text in texts:
        if b"\r\n" in text:
            newline = b"\r\n"
        if out and not out.endswith(b"\n"):
            out += newline
        out += text
    return bytes(out)


def write_empire_file(path: Path, data: bytes, backup_dir: Path) -> None:
    """Back the file up, then replace it at once. Raises OSError, before
    anything is written if the backup fails."""
    backup_file(path, backup_dir)
    tmp = path.with_name(f".{path.name}.cold-steel.tmp")
    try:
        tmp.write_bytes(data)
        if path.exists():
            tmp.chmod(path.stat().st_mode & 0o777)
        tmp.replace(path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


class EmpireReader:
    """Reads the empire file again only when its size or timestamp changed."""

    def __init__(self) -> None:
        self._held: tuple[Path, tuple[int, int], EmpireFile] | None = None

    def read(self, path: Path) -> EmpireFile:
        """Raises OSError if it can't be read."""
        st = path.stat()
        stamp = st.st_size, st.st_mtime_ns
        held = self._held
        if held is not None and held[0] == path and held[1] == stamp:
            return held[2]
        found = read_empire_file(path)
        self._held = path, stamp, found
        return found


# Reading one block

# The fields that name something a mod can add, and the folder that defines it.
_FIELDS = {
    "authority": "common/governments/authorities",
    "government": "common/governments",
    "origin": "common/governments/civics",
    "ethic": "common/ethics",
    "planet_class": "common/planet_classes",
    "initializer": "common/solar_system_initializers",
    "graphical_culture": "common/graphical_culture",
    "city_graphical_culture": "common/graphical_culture",
}
_SPECIES_FIELDS = {
    "class": "common/species_classes",
    "trait": "common/traits",
    "name_list": "common/name_lists",
}


def _empire(name: str, text: bytes) -> Empire:
    try:
        nodes = parse(text.decode("utf-8-sig", errors="replace"))
    except ParseError as error:
        return Empire(name, text, None, f"Its block is broken: {error}")
    body = next((n.value for n in nodes if isinstance(n.value, tuple)), None)
    if body is None:
        return Empire(name, text, None, "Not an empire block")
    return Empire(name, text, _info(body))


def _info(body: tuple[Node, ...]) -> EmpireInfo:
    uses: list[Use] = []
    plain: dict[str, str] = {}
    ethics: list[str] = []
    civics: list[str] = []
    species_name = ""
    for node in body:
        key, value = node.key or "", node.value
        if isinstance(value, str):
            if key == "ethic":
                ethics.append(value)
            plain.setdefault(key, value)
            if key in _FIELDS and value:
                uses.append((_FIELDS[key], value))
        elif key == "civics":
            civics += _strings(value)
            uses += (("common/governments/civics", c) for c in _strings(value))
        elif key == "species":
            for child in value:
                if isinstance(child.value, str) and child.key in _SPECIES_FIELDS and child.value:
                    uses.append((_SPECIES_FIELDS[child.key], child.value))
                elif child.key == "species_name" and isinstance(child.value, tuple):
                    species_name = _field(child.value, "key")
        elif key == "ruler":
            uses += (
                ("common/traits", c.value)
                for c in value
                if c.key == "trait" and isinstance(c.value, str) and c.value
            )
    return EmpireInfo(
        species=species_name,
        authority=plain.get("authority", ""),
        government=plain.get("government", ""),
        origin=plain.get("origin", ""),
        ethics=tuple(ethics),
        civics=tuple(civics),
        uses=tuple(dict.fromkeys(uses)),  # once each, in order
    )


def _strings(nodes: tuple[Node, ...]) -> list[str]:
    return [n.value for n in nodes if n.key is None and isinstance(n.value, str)]


def _field(nodes: tuple[Node, ...], key: str) -> str:
    return next((n.value for n in nodes if n.key == key and isinstance(n.value, str)), "")


def _text(raw: bytes) -> str:
    return raw.strip(b'"').decode("utf-8", "replace")
