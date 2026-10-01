"""The small text formats: Paradox script, `.mod` descriptors and Valve's `.vdf`."""

import pytest

from cold_steel.paradox.descriptor import Descriptor, parse_descriptor
from cold_steel.paradox.script import Node, ParseError, parse
from cold_steel.paradox.vdf import VdfError, parse_vdf


def test_script_reads_values_blocks_and_comments() -> None:
    nodes = parse(
        """
        # a comment
        name = "Quoted \\"name\\""   # trailing comment
        count=3
        tags = { "a" b }
        limit = { size >= 2 }
        """
    )
    assert nodes[0] == Node("name", "=", 'Quoted "name"', 3)
    assert nodes[1] == Node("count", "=", "3", 4)
    assert nodes[2].value == (Node(None, None, "a", 5), Node(None, None, "b", 5))
    assert nodes[3].value == (Node("size", ">=", "2", 6),)


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ("a = { b = 1", "never closed"),
        ("a = 1 }", "no matching"),
        ("= 1", "nothing before it"),
        ("a =", "has no value"),
        ('a = "open', "unclosed string"),
    ],
)
def test_script_errors_name_the_problem(text: str, message: str) -> None:
    with pytest.raises(ParseError, match=message):
        parse(text)


def test_descriptor_reads_known_keys_and_ignores_others() -> None:
    desc = parse_descriptor(
        'name="Mod"\nversion="1.0"\ntags={\n\t"Sound"\n\t"Music"\n}\n'
        'supported_version="v4.5.*"\nunknown_key="x"\ndependencies={ "Other Mod" }\n'
    )
    assert desc == Descriptor(
        name="Mod",
        version="1.0",
        tags=("Sound", "Music"),
        supported_version="v4.5.*",
        dependencies=("Other Mod",),
    )


def test_descriptor_fill_from_keeps_own_values() -> None:
    mine = Descriptor(name="Mine", tags=("A",))
    theirs = Descriptor(name="Theirs", version="2", tags=("B",))
    assert mine.fill_from(theirs) == Descriptor(name="Mine", version="2", tags=("A",))


def test_vdf_reads_nested_blocks_and_escapes() -> None:
    data = parse_vdf('"root"\n{\n  // comment\n  "path"  "C:\\\\Games"\n  "inner" { "k" "v" }\n}\n')
    assert data == {"root": {"path": "C:\\Games", "inner": {"k": "v"}}}


def test_vdf_rejects_unbalanced_braces() -> None:
    with pytest.raises(VdfError):
        parse_vdf('"root" { "a" "b"')
    with pytest.raises(VdfError):
        parse_vdf('"a" "b" }')
