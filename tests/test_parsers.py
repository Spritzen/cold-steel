"""The small text formats: Paradox script, `.mod` descriptors and Valve's `.vdf`."""

import pytest

from cold_steel.paradox.descriptor import Descriptor, parse_descriptor
from cold_steel.paradox.localisation import keys as loc_keys
from cold_steel.paradox.localisation import language
from cold_steel.paradox.script import Node, ParseError, children, parse, scan, value_of
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


def keys(data: bytes) -> list[tuple[bytes, int]]:
    return [(e.key, e.line) for e in scan(data)]


def test_scan_finds_top_level_entries_and_their_lines() -> None:
    data = (
        b"# a comment with { and }\n"
        b"tech_a = {\n"
        b'\tname = "a } in a string"  # and } in a comment\n'
        b"\tinner = { x = 1 }\n"
        b"}\n"
        b"@var = 3\n"
        b'"quoted key" = yes\n'
        b"tech_b={cost=2}"
    )
    entries = scan(data)
    assert [(e.key, e.line, e.block) for e in entries] == [
        (b"tech_a", 2, True),
        (b"@var", 6, False),
        (b'"quoted key"', 7, False),
        (b"tech_b", 8, True),
    ]
    a = entries[0]
    assert data[a.start : a.end].endswith(b"x = 1 }\n}")
    assert data[entries[1].value : entries[1].end] == b"3"


def test_scan_reads_inside_a_block() -> None:
    data = b'namespace = x\ncountry_event = {\n\tid = x.1\n\ttitle = "T"\n}\n'
    event = scan(data)[1]
    assert [(c.key, c.line) for c in children(data, event)] == [(b"id", 3), (b"title", 4)]
    assert value_of(data, event, b"id") == b"x.1"
    assert value_of(data, event, b"title") == b"T"
    assert value_of(data, event, b"missing") is None


@pytest.mark.parametrize(
    ("data", "expected"),
    [
        # A file of nothing but comments once made the regex hang.
        (b"#" * 40 + b"\n# title #\n" + b"#" * 40, []),
        (b"", []),
        # A stray "}" is skipped and reading goes on.
        (b"a = 1\n}\nb = 2", [(b"a", 1), (b"b", 3)]),
        # A "{" never closed runs to the end of the file, as in the game.
        (b"a = { b = 1\nc = 2", [(b"a", 1)]),
        # Bare values, unnamed blocks and lone operators are not entries.
        (b"just_a_value\n{ x = 1 }\n= 3\nreal = yes", [(b"real", 4)]),
        (b"a = ", []),
        (b"limit >= 2", [(b"limit", 1)]),
        (b'a = "unclosed\nb = 1', [(b"a", 1), (b"b", 2)]),
    ],
)
def test_scan_reads_broken_input_as_best_it_can(
    data: bytes, expected: list[tuple[bytes, int]]
) -> None:
    assert keys(data) == expected


def test_localisation_reads_language_and_keys() -> None:
    data = (
        b"\xef\xbb\xbf# a comment first\n"
        b"l_english:\n"
        b' tech_a:0 "Red Lasers"\n'
        b' tech_a_desc: "No version number" # comment\r\n'
        b'# KEY: "commented out"\n'
        b" broken line\n"
    )
    assert language(data) == "l_english"
    assert [(e.key, e.line, data[e.value : e.end]) for e in loc_keys(data)] == [
        (b"tech_a", 3, b'"Red Lasers"'),
        (b"tech_a_desc", 4, b'"No version number" # comment'),
    ]
    assert language(b' KEY: "no header"') == ""
