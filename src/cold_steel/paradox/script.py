"""A parser for Paradox script: the `key = value` and `key = { ... }` format used by
`.mod` descriptors and by every game file.

    name="UI Overhaul Dynamic"
    tags={
        "Fixes"
        "Graphics"
    }

There are two readers:

- `parse()` builds the whole tree. It is used for small files like descriptors.
- `scan()` only finds where each top-level entry starts and ends. Conflict
  checks need nothing more, and it reads about 60 MB of game script a second.
  It never fails: broken input gives fewer or odder entries, the way the game
  reads it as best it can.
"""

import re
from collections.abc import Iterator
from dataclasses import dataclass
from typing import NamedTuple


class ParseError(ValueError):
    def __init__(self, message: str, line: int) -> None:
        super().__init__(f"line {line}: {message}")
        self.line = line


@dataclass(frozen=True, slots=True)
class Node:
    """One entry. `key = value`, or a bare value inside a block (`key` is None).

    `value` is a string, or a tuple of child nodes for a `{ ... }` block.
    """

    key: str | None
    op: str | None
    value: str | tuple[Node, ...]
    line: int


_TOKEN = re.compile(
    r"""
      (?P<space>\s+)
    | (?P<comment>\#[^\n]*)
    | (?P<string>"(?:[^"\\]|\\.)*")
    | (?P<op>[<>!=?]=|[<>=])
    | (?P<brace>[{}])
    | (?P<word>[^\s{}=<>!?"\#]+)
    """,
    re.VERBOSE,
)

type _Token = tuple[str, str, int]  # kind, text, line


def _tokens(text: str) -> list[_Token]:
    tokens: list[_Token] = []
    line = 1
    pos = 0
    while pos < len(text):
        match = _TOKEN.match(text, pos)
        if match is None:
            what = "unclosed string" if text[pos] == '"' else f"unexpected {text[pos]!r}"
            raise ParseError(what, line)
        kind = match.lastgroup
        assert kind is not None
        chunk = match.group()
        if kind == "string":
            tokens.append(("str", chunk[1:-1].replace('\\"', '"'), line))
        elif kind not in ("space", "comment"):
            tokens.append((kind, chunk, line))
        line += chunk.count("\n")
        pos = match.end()
    return tokens


def parse(text: str) -> tuple[Node, ...]:
    """Parse a whole file. Raises `ParseError` on broken input."""
    tokens = _tokens(text)
    nodes, end = _block(tokens, 0, top=True)
    assert end == len(tokens)
    return nodes


def _block(tokens: list[_Token], i: int, *, top: bool) -> tuple[tuple[Node, ...], int]:
    nodes: list[Node] = []
    while i < len(tokens):
        kind, text, line = tokens[i]
        if kind == "brace" and text == "}":
            if top:
                raise ParseError("'}' with no matching '{'", line)
            return tuple(nodes), i + 1
        if kind == "op":
            raise ParseError(f"{text!r} with nothing before it", line)
        if kind == "brace":  # an unnamed block: { ... }
            children, i = _block(tokens, i + 1, top=False)
            nodes.append(Node(None, None, children, line))
            continue
        # A word or string: either a bare value, or the key of `key = value`.
        if i + 1 < len(tokens) and tokens[i + 1][0] == "op":
            op = tokens[i + 1][1]
            if i + 2 >= len(tokens):
                raise ParseError(f"{text} {op} has no value", line)
            vkind, vtext, _ = tokens[i + 2]
            if vkind == "brace" and vtext == "{":
                children, i = _block(tokens, i + 3, top=False)
                nodes.append(Node(text, op, children, line))
            elif vkind in ("word", "str"):
                nodes.append(Node(text, op, vtext, line))
                i += 3
            else:
                raise ParseError(f"{text} {op} has no value", line)
        else:
            nodes.append(Node(None, None, text, line))
            i += 1
    if not top:
        raise ParseError("'{' is never closed", tokens[-1][2] if tokens else 1)
    return tuple(nodes), i


# The fast reader. It works on bytes, so files in any encoding can be read
# without decoding them first.

# Whitespace and comments. Possessive (`++`, `*+`) so a line of "#####" can't
# make the regex try every way of splitting it.
_GAP = re.compile(rb"(?:\s++|#[^\n]*+)*+")
_WORD = re.compile(rb'"[^"\n]*+"?|[^\s{}=<>!?"#]++')
_OP = re.compile(rb"[<>!?=]=|[<>=]")
# Inside a block only braces matter, and strings or comments can hide them.
_INNER = re.compile(rb'[{}"#]')


class Entry(NamedTuple):
    """One top-level `key = value` or `key = { ... }`, by its place in the bytes."""

    key: bytes  # as written, quotes included
    start: int  # where the key starts
    value: int  # where the value starts: its first byte, or the block's "{"
    end: int  # just after the value, or after the block's "}"
    line: int  # the key's line, counting from 1
    block: bool  # the value is a { ... } block

    @property
    def inside(self) -> tuple[int, int]:
        """Where a block's contents start and end, without its braces."""
        return self.value + 1, max(self.value + 1, self.end - 1)


def scan(data: bytes, start: int = 0, end: int | None = None, line: int = 1) -> list[Entry]:
    """The `key = value` entries between `start` and `end`, not looking inside blocks.

    To read inside a block, scan again between `entry.inside`, starting at
    `entry.line`. Bare values and unnamed blocks are skipped.
    """
    return list(iter_scan(data, start, end, line))


def iter_scan(
    data: bytes, start: int = 0, end: int | None = None, line: int = 1
) -> Iterator[Entry]:
    """`scan()`, one entry at a time, for callers that may stop early."""
    stop = len(data) if end is None else end
    word, op = _WORD.match, _OP.match
    pos = start
    counted = start  # line numbers are counted up to here
    while True:
        pos = _after_gap(data, pos, stop)
        if pos >= stop:
            return
        char = data[pos]
        if char == 0x7D:  # a stray "}"
            pos += 1
            continue
        if char == 0x7B:  # an unnamed block
            pos = _skip_block(data, pos + 1, stop)
            continue
        key = word(data, pos, stop)
        if key is None:  # an operator with nothing before it
            pos += 1
            continue
        key_start = pos
        pos = _after_gap(data, key.end(), stop)
        operator = op(data, pos, stop)
        if operator is None:  # a bare value
            continue
        value_start = pos = _after_gap(data, operator.end(), stop)
        block = pos < stop and data[pos] == 0x7B
        if block:
            pos = _skip_block(data, pos + 1, stop)
        else:
            value = word(data, pos, stop)
            if value is None:
                continue
            pos = value.end()
        line += data.count(b"\n", counted, key_start)
        counted = key_start
        yield Entry(key.group(), key_start, value_start, pos, line, block)


def _after_gap(data: bytes, pos: int, stop: int) -> int:
    gap = _GAP.match(data, pos, stop)
    assert gap is not None  # it can match nothing, so it always matches
    return gap.end()


def _skip_block(data: bytes, pos: int, stop: int) -> int:
    """`pos` is just inside a "{". Returns the place just after its "}"."""
    depth = 1
    search, find = _INNER.search, data.find
    while True:
        found = search(data, pos, stop)
        if found is None:
            return stop  # never closed: the game closes it at the end of the file
        char = data[found.start()]
        pos = found.end()
        if char == 0x7B:
            depth += 1
        elif char == 0x7D:
            depth -= 1
            if not depth:
                return pos
        else:  # a string or a comment, which ends at the end of the line
            newline = find(b"\n", pos, stop)
            if char == 0x22:
                quote = find(b'"', pos, stop)
                if quote != -1 and (newline == -1 or quote < newline):
                    pos = quote + 1
                    continue
            if newline == -1:
                return stop
            pos = newline


def children(data: bytes, entry: Entry) -> list[Entry]:
    """The entries directly inside `entry`'s block."""
    if not entry.block:
        return []
    start, end = entry.inside
    return scan(data, start, end, entry.line)


def value_of(data: bytes, entry: Entry, name: bytes) -> bytes | None:
    """The plain value of `name = value` directly inside `entry`'s block, unquoted."""
    if not entry.block:
        return None
    start, end = entry.inside
    for child in iter_scan(data, start, end, entry.line):
        if child.key == name and not child.block:
            return data[child.value : child.end].strip(b'"')
    return None
