"""A parser for Paradox script: the `key = value` and `key = { ... }` format used by
`.mod` descriptors and, later, by every game file.

    name="UI Overhaul Dynamic"
    tags={
        "Fixes"
        "Graphics"
    }

Phase 1 only reads descriptors with it. Phase 4 grows it to read game files.
"""

import re
from dataclasses import dataclass


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
