"""Reads Valve's text KeyValues format (`libraryfolders.vdf`, `appmanifest_*.acf`).

"libraryfolders"
{
    "0"
    {
        "path"  "/home/me/.local/share/Steam"
    }
}
"""

import re

type VdfValue = str | dict[str, VdfValue]

_TOKEN = re.compile(r'\s+|//[^\n]*|"((?:[^"\\]|\\.)*)"|([{}])|([^\s{}"]+)')
_ESCAPES = {"n": "\n", "t": "\t", "\\": "\\", '"': '"'}


class VdfError(ValueError):
    pass


def parse_vdf(text: str) -> dict[str, VdfValue]:
    """A repeated key keeps its last value."""
    root: dict[str, VdfValue] = {}
    stack = [root]
    key: str | None = None
    pos = 0
    while pos < len(text):
        match = _TOKEN.match(text, pos)
        if match is None:
            raise VdfError(f"unexpected {text[pos]!r} at offset {pos}")
        pos = match.end()
        quoted, brace, bare = match.groups()
        if brace == "{":
            if key is None:
                raise VdfError(f"block with no name at offset {match.start()}")
            child: dict[str, VdfValue] = {}
            stack[-1][key] = child
            stack.append(child)
            key = None
        elif brace == "}":
            if len(stack) == 1 or key is not None:
                raise VdfError(f"unexpected '}}' at offset {match.start()}")
            stack.pop()
        elif quoted is not None or bare is not None:
            word = _unescape(quoted) if quoted is not None else bare
            assert word is not None
            if key is None:
                key = word
            else:
                stack[-1][key] = word
                key = None
    if len(stack) != 1:
        raise VdfError("a block is never closed")
    return root


def _unescape(text: str) -> str:
    if "\\" not in text:
        return text
    return re.sub(r"\\(.)", lambda m: _ESCAPES.get(m[1], "\\" + m[1]), text)
