"""Reads `.mod` descriptor files: `descriptor.mod` inside a mod, and the
`mod/*.mod` files the game loads. See docs/reference/stellaris-files.md.
"""

import msgspec

from cold_steel.paradox.script import Node, parse


class Descriptor(msgspec.Struct, frozen=True):
    name: str = ""
    version: str = ""
    supported_version: str = ""
    tags: tuple[str, ...] = ()
    picture: str = ""
    path: str = ""
    archive: str = ""
    remote_file_id: str = ""
    dependencies: tuple[str, ...] = ()

    def fill_from(self, other: Descriptor) -> Descriptor:
        """This descriptor, with any blank fields taken from `other`."""
        fields = {f: getattr(self, f) or getattr(other, f) for f in self.__struct_fields__}
        return Descriptor(**fields)


_TEXT = ("name", "version", "supported_version", "picture", "path", "archive", "remote_file_id")
_LISTS = ("tags", "dependencies")


def parse_descriptor(text: str) -> Descriptor:
    """Raises `script.ParseError` if the file is broken. Unknown keys are ignored."""
    fields: dict[str, str | tuple[str, ...]] = {}
    for node in parse(text):
        if node.key in _TEXT and isinstance(node.value, str):
            fields[node.key] = node.value
        elif node.key in _LISTS and isinstance(node.value, tuple):
            fields[node.key] = _strings(node.value)
    return Descriptor(**fields)  # type: ignore[arg-type]


def decode_descriptor(data: bytes) -> Descriptor:
    # Paradox files are UTF-8, often with a byte-order mark.
    return parse_descriptor(data.decode("utf-8-sig", errors="replace"))


def _strings(nodes: tuple[Node, ...]) -> tuple[str, ...]:
    return tuple(n.value for n in nodes if n.key is None and isinstance(n.value, str))
