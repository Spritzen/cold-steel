"""Reading one file into the game objects it defines.

    read_definitions("common/technology/x.txt", data, rules.for_file(path), "l_english")
    -> (Definition(kind="common/technology", key="tech_lasers_1", ...), ...)

Which objects a file holds, and what each is called, depends on its folder's
rule (see merge_rules.py). Positions are byte offsets into the file, so the
viewer can show each definition without parsing again.
"""

import re

import msgspec
import xxhash

from cold_steel.core.merge_rules import Rule
from cold_steel.paradox import localisation
from cold_steel.paradox.script import Entry, children, scan, value_of

# Keys that set something up for the file rather than defining an object.
_NOT_OBJECTS = frozenset({b"namespace"})

_COMMENT = re.compile(rb"#[^\n]*")


_PADDING = tuple((pad, pad.strip()) for c in (b"=", b"{", b"}") for pad in (b" " + c, c + b" "))


class Definition(msgspec.Struct, frozen=True, array_like=True):
    kind: str  # the object type: a folder like "common/technology", or "localisation"
    key: str  # the object's name, unique within its kind
    start: int  # byte offsets of the whole definition in its file
    end: int
    line: int
    digest: int  # changes only when the meaning does: whitespace and comments don't count


_NAMED_LANGUAGE = re.compile(
    r"l_(english|braz_por|french|german|polish|russian|spanish|japanese|korean|simp_chinese)",
    re.IGNORECASE,
)


def worth_reading(path: str, rule: Rule, language: str) -> bool:
    """False for files that can't define objects: no rule to read them, or
    localisation whose name says it's in another language. Those are most of a
    big mod's localisation, so skipping them saves reading hundreds of MB.
    """
    if rule.unit == "file":
        return False
    if rule.unit == "localisation":
        named = _NAMED_LANGUAGE.search(path.rpartition("/")[2])
        return named is None or named.group(0).lower() == language.lower()
    return True


def read_definitions(path: str, data: bytes, rule: Rule, language: str) -> tuple[Definition, ...]:
    """Every object `data` defines, in file order. Empty for a "file" rule."""
    folder = path.rpartition("/")[0].lower()
    match rule.unit:
        case "file":
            return ()
        case "localisation":
            if localisation.language(data) != language:
                return ()
            return tuple(
                Definition(
                    "localisation", _text(e.key), e.start, e.end, e.line, _loc_digest(data, e)
                )
                for e in localisation.keys(data)
            )
        case "define":
            return tuple(
                _definition(folder, f"{_text(group.key)}.{_text(e.key)}", data, e)
                for group in scan(data)
                for e in children(data, group)
            )
        case "field":
            field = rule.field.encode()
            found: list[Definition] = []
            for e in scan(data):
                if e.key in _NOT_OBJECTS:
                    continue
                name = value_of(data, e, field) if e.block else None
                if name:
                    found.append(_definition(folder, _text(name), data, e))
            return tuple(found)
        case "name":
            return tuple(_named(data, folder))
        case "key":
            return tuple(
                _definition(folder, _text(e.key), data, e)
                for e in scan(data)
                if e.key not in _NOT_OBJECTS
                and (folder.endswith("scripted_variables") or not e.key.startswith(b"@"))
            )


def _named(data: bytes, folder: str) -> list[Definition]:
    """Graphics and interface files: objects are blocks with a `name = ...` field.

    Most files wrap them, as in `spriteTypes = { spriteType = { name = "GFX_x" } }`,
    so a block without a name is looked inside once. A block with no named
    blocks inside is an object named by its key.
    """
    found: list[Definition] = []
    for entry in scan(data):
        if not entry.block or entry.key.startswith(b"@"):
            continue
        name = b""
        inner: list[tuple[Entry, bytes]] = []
        for child in children(data, entry):
            if not child.block:
                if child.key == b"name":
                    name = data[child.value : child.end].strip(b'"')
                    break
            elif child_name := value_of(data, child, b"name"):
                inner.append((child, child_name))
        if name:
            found.append(_definition(_text(entry.key).lower(), _text(name), data, entry))
        elif inner:
            found += (_definition(_text(c.key).lower(), _text(n), data, c) for c, n in inner)
        else:
            found.append(_definition(folder, _text(entry.key), data, entry))
    return found


def _definition(kind: str, key: str, data: bytes, entry: Entry) -> Definition:
    return Definition(
        kind, key, entry.start, entry.end, entry.line, digest(data[entry.start : entry.end])
    )


def _loc_digest(data: bytes, entry: Entry) -> int:
    # The text alone: a trailing comment or the :0 doesn't change what's shown.
    value = data[entry.value : entry.end]
    end = value.rfind(b'"')
    return xxhash.xxh3_64_intdigest(value[: end + 1] if end > 0 else value)


def digest(text: bytes) -> int:
    """A hash of script that ignores comments, spacing and line breaks.

    Irony's DefinitionSHA. It errs toward calling two texts the same: a "#"
    inside a quoted string counts as a comment.
    """
    if b"#" in text:
        text = _COMMENT.sub(b"", text)
    # bytes methods run in C: much faster than regular expressions here.
    text = b" ".join(text.split())
    for padded, bare in _PADDING:
        text = text.replace(padded, bare)
    return xxhash.xxh3_64_intdigest(text)


def _text(raw: bytes) -> str:
    return raw.strip(b'"').decode("utf-8", "replace")
