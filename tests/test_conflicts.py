"""The object index and the conflict finder, run against small sample mods.

Alpha, Beta and My Local are the sample install's mods. Each test gives them
(and the game) a few files, then asks who wins.
"""

from collections.abc import Mapping
from pathlib import Path

import pytest

from cold_steel.core import index as index_module
from cold_steel.core.conflicts import FILE, Conflict, ConflictFinder, Found
from cold_steel.core.definitions import Definition, read_definitions
from cold_steel.core.index import GAME, Index, Indexer
from cold_steel.core.jobs import JobContext
from cold_steel.core.library import Library, Scanner
from cold_steel.core.merge_rules import Rule
from cold_steel.store.playsets import Playset, PlaysetEntry
from conftest import SampleInstall

ALPHA = "workshop:2000000001"
BETA = "workshop:2000000002"
LOCAL = "local:my_local"
BOM = b"\xef\xbb\xbf"


def folders(install: SampleInstall) -> dict[str, Path]:
    return {
        GAME: install.root / "games/steamapps/common/Stellaris",
        ALPHA: install.workshop_dir / "2000000001",
        BETA: install.workshop_dir / "2000000002",
        LOCAL: install.data_dir / "mod/my_local",
    }


def write(install: SampleInstall, files: Mapping[str, Mapping[str, bytes]]) -> None:
    for layer, layer_files in files.items():
        for name, data in layer_files.items():
            path = folders(install)[layer] / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)


def scan(install: SampleInstall) -> Library:
    return Scanner(*install.scanner_args())(JobContext())


def index(install: SampleInstall, library: Library, previous: Index | None = None) -> Index:
    cache = install.cache_file.parent / "index"
    return Indexer(library, cache, previous, workers=1)(JobContext())


def find(install: SampleInstall, *order: str, disabled: tuple[str, ...] = ()) -> Found:
    library = scan(install)
    entries = [PlaysetEntry(key, enabled=key not in disabled) for key in order]
    playset = Playset(id="p", name="Test", entries=tuple(entries))
    return ConflictFinder(index(install, library), playset, library)(JobContext())


def conflict(found: Found, kind: str, key: str) -> Conflict:
    matches = [c for c in found.conflicts if c.kind == kind and c.key == key]
    assert len(matches) == 1, [(c.kind, c.key) for c in found.conflicts]
    return matches[0]


def test_last_file_name_wins_whatever_the_load_order(sample_install: SampleInstall) -> None:
    write(
        sample_install,
        {
            ALPHA: {"common/technology/b_alpha.txt": b"tech_x = { cost = 1 }\n"},
            BETA: {"common/technology/a_beta.txt": b"tech_x = { cost = 2 }\n"},
        },
    )
    # Beta loads after Alpha, but Alpha's file name sorts last.
    found = find(sample_install, ALPHA, BETA)

    tech = conflict(found, "common/technology", "tech_x")
    assert tech.winning.layer == ALPHA
    assert "b_alpha.txt sorts last" in tech.reason
    assert not tech.identical
    assert tech.mods == (BETA, ALPHA)  # in the order the game reads them


def test_first_file_name_wins_in_a_first_wins_folder(sample_install: SampleInstall) -> None:
    write(
        sample_install,
        {
            ALPHA: {"common/traits/00_alpha.txt": b"trait_x = { cost = 1 }\n"},
            BETA: {"common/traits/zz_beta.txt": b"trait_x = { cost = 2 }\n"},
        },
    )
    trait = conflict(find(sample_install, ALPHA, BETA), "common/traits", "trait_x")
    assert trait.winning.layer == ALPHA
    assert "keeps the first" in trait.reason


def test_the_same_file_name_leaves_it_to_load_order(sample_install: SampleInstall) -> None:
    # Sprites are one kind of object across interface/ and its folders.
    sprite = b'spriteTypes = { spriteType = { name = "GFX_x" texturefile = "%s" } }\n'
    write(
        sample_install,
        {
            ALPHA: {"interface/sub/same.gfx": sprite % b"a.dds"},
            BETA: {"interface/same.gfx": sprite % b"b.dds"},
        },
    )
    sprite_conflict = conflict(find(sample_install, BETA, ALPHA), "spritetype", "GFX_x")
    assert sprite_conflict.winning.layer == ALPHA
    assert "load order decides" in sprite_conflict.reason


def test_a_file_at_the_same_path_replaces_the_earlier_one(sample_install: SampleInstall) -> None:
    write(
        sample_install,
        {
            GAME: {"common/technology/00_tech.txt": b"tech_x = { cost = 0 }\n"},
            ALPHA: {"common/technology/00_tech.txt": b"tech_x = { cost = 1 }\n"},
            BETA: {"common/technology/00_tech.txt": b"tech_x = { cost = 2 }\n"},
        },
    )
    found = find(sample_install, BETA, ALPHA)

    file = conflict(found, FILE, "common/technology/00_tech.txt")
    assert [c.layer for c in file.claims] == [GAME, BETA, ALPHA]
    assert file.winning.layer == ALPHA
    # Only the winning file is read, so its objects don't clash with the others.
    assert not [c for c in found.conflicts if c.kind == "common/technology"]
    assert [c.layer for c in found.claims("common/technology", "tech_x")] == [ALPHA]


def test_the_game_takes_part_but_only_two_mods_make_a_clash(
    sample_install: SampleInstall,
) -> None:
    write(
        sample_install,
        {
            GAME: {"common/traits/00_traits.txt": b"trait_x = { cost = 0 }\n"},
            ALPHA: {"common/traits/zz_alpha.txt": b"trait_x = { cost = 1 }\n"},
        },
    )
    trait = conflict(find(sample_install, ALPHA), "common/traits", "trait_x")
    # The game's 00_ file sorts first in a first-wins folder: the mod never takes effect.
    assert trait.winning.layer == GAME
    assert trait.mods == (ALPHA,)


def test_events_are_named_by_their_id(sample_install: SampleInstall) -> None:
    event = b"namespace = x\ncountry_event = {\n\tid = x.1\n\ttitle = %s\n}\n"
    write(
        sample_install,
        {
            ALPHA: {"events/a.txt": event % b"A"},
            BETA: {"events/b.txt": event % b"B"},
        },
    )
    found = find(sample_install, ALPHA, BETA)
    assert conflict(found, "events", "x.1").winning.layer == ALPHA  # events: first wins
    assert not found.claims("events", "namespace")
    assert [r[1] for r in found.search("x.", 10)] == ["x.1"]


def test_defines_clash_one_value_at_a_time(sample_install: SampleInstall) -> None:
    write(
        sample_install,
        {
            ALPHA: {"common/defines/a.txt": b"NGame = { A = 1 B = 2 }\n"},
            BETA: {"common/defines/b.txt": b"NGame = { B = 3 }\nNShip = { A = 1 }\n"},
        },
    )
    found = find(sample_install, ALPHA, BETA)
    assert conflict(found, "common/defines", "NGame.B").winning.layer == BETA
    assert not [c for c in found.conflicts if c.key in ("NGame.A", "NShip.A")]


def test_sprites_are_named_inside_their_wrapper(sample_install: SampleInstall) -> None:
    sprite = b'spriteTypes = {\n\tspriteType = { name = "GFX_x" texturefile = "%s" }\n}\n'
    write(
        sample_install,
        {
            ALPHA: {"interface/a.gfx": sprite % b"a.dds"},
            BETA: {"interface/b.gfx": sprite % b"b.dds"},
        },
    )
    sprite_conflict = conflict(find(sample_install, ALPHA, BETA), "spritetype", "GFX_x")
    assert sprite_conflict.winning.layer == BETA
    assert sprite_conflict.winning.definition is not None
    assert sprite_conflict.winning.definition.line == 2


def test_localisation_replace_folder_wins(sample_install: SampleInstall) -> None:
    def loc(text: bytes) -> bytes:
        return BOM + b'l_english:\n KEY:0 "' + text + b'"\n'

    write(
        sample_install,
        {
            GAME: {"localisation/english/zz_l_english.yml": loc(b"game")},
            ALPHA: {"localisation/english/replace/a_l_english.yml": loc(b"alpha")},
            BETA: {
                "localisation/english/zz_beta_l_english.yml": loc(b"beta"),
                "localisation/french/zz_beta_l_french.yml": loc(b"beta").replace(
                    b"l_english", b"l_french"
                ),
            },
        },
    )
    key = conflict(find(sample_install, ALPHA, BETA), "localisation", "KEY")
    assert key.winning.layer == ALPHA
    assert "replace/" in key.reason
    # Only the game's language is read.
    assert [c.path for c in key.claims if c.layer == BETA] == [
        "localisation/english/zz_beta_l_english.yml"
    ]


def test_identical_definitions_are_marked(sample_install: SampleInstall) -> None:
    write(
        sample_install,
        {
            ALPHA: {"common/technology/a.txt": b"tech_x = {\n\tcost = 1 # cheap\n}\n"},
            BETA: {"common/technology/b.txt": b"tech_x={ cost=1 }"},
        },
    )
    assert conflict(find(sample_install, ALPHA, BETA), "common/technology", "tech_x").identical


def test_merged_folders_and_disabled_mods_never_clash(sample_install: SampleInstall) -> None:
    write(
        sample_install,
        {
            ALPHA: {"common/on_actions/a.txt": b"on_game_start = { events = { a.1 } }\n"},
            BETA: {"common/on_actions/b.txt": b"on_game_start = { events = { b.1 } }\n"},
            LOCAL: {"common/technology/l.txt": b"tech_x = { }\n"},
        },
    )
    write(sample_install, {BETA: {"common/technology/b.txt": b"tech_x = { cost = 2 }\n"}})
    found = find(sample_install, ALPHA, BETA, LOCAL, disabled=(LOCAL,))
    assert not [c for c in found.conflicts if c.kind != FILE]
    assert LOCAL not in found.order


def test_zipped_mods_are_indexed(sample_install: SampleInstall) -> None:
    library = scan(sample_install)
    gamma = index(sample_install, library).layers["workshop:2000000003"]
    assert "music/gamma.txt" in gamma.files


# The cache


@pytest.fixture
def parse_count(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    parsed: list[str] = []
    real = read_definitions

    def counting(path: str, data: bytes, rule: Rule, language: str) -> tuple[Definition, ...]:
        parsed.append(path)
        return real(path, data, rule, language)

    monkeypatch.setattr(index_module, "read_definitions", counting)
    return parsed


def test_unchanged_files_are_never_parsed_twice(
    sample_install: SampleInstall, parse_count: list[str]
) -> None:
    write(
        sample_install,
        {
            ALPHA: {"common/technology/a.txt": b"tech_a = { }\n"},
            BETA: {"common/technology/b.txt": b"tech_b = { }\n"},
        },
    )
    first = index(sample_install, scan(sample_install))
    assert "common/technology/a.txt" in parse_count

    # From the cache files, then from the last index in memory.
    parse_count.clear()
    assert index(sample_install, scan(sample_install)).layers == first.layers
    assert index(sample_install, scan(sample_install), first).layers == first.layers
    assert parse_count == []

    # A new timestamp on the same bytes: hashed, but not parsed again.
    path = folders(sample_install)[ALPHA] / "common/technology/a.txt"
    path.touch()
    second = index(sample_install, scan(sample_install), first)
    assert parse_count == []
    assert second.layers[ALPHA].files["common/technology/a.txt"].definitions

    # Changed bytes in one mod: only that file is parsed.
    path.write_bytes(b"tech_a = { }\ntech_c = { }\n")
    third = index(sample_install, scan(sample_install), second)
    assert parse_count == ["common/technology/a.txt"]
    assert third.layers[BETA] is second.layers[BETA]
    keys = [d.key for d in third.layers[ALPHA].files["common/technology/a.txt"].definitions]
    assert keys == ["tech_a", "tech_c"]


def test_parallel_reading_matches_reading_in_one_process(
    sample_install: SampleInstall, monkeypatch: pytest.MonkeyPatch
) -> None:
    write(
        sample_install,
        {
            layer: {f"common/technology/{n}.txt": b"tech_%d = { }\n" % n for n in range(20)}
            for layer in (ALPHA, BETA, LOCAL)
        },
    )
    library = scan(sample_install)
    alone = Indexer(library, sample_install.cache_file.parent / "one", workers=1)(JobContext())
    monkeypatch.setattr(index_module, "PARALLEL_BYTES", 0)
    monkeypatch.setattr(index_module, "CHUNK_BYTES", 50)
    shared = Indexer(library, sample_install.cache_file.parent / "many", workers=3)(JobContext())
    assert shared.layers == alone.layers


def test_cache_files_of_removed_mods_are_deleted(sample_install: SampleInstall) -> None:
    cache = sample_install.cache_file.parent / "index"
    index(sample_install, scan(sample_install))
    kept = sorted(p.name for p in cache.iterdir())
    assert "game.msgpack" in kept and "workshop-2000000001.msgpack" in kept

    (cache / "workshop-999.msgpack").write_bytes(b"from a mod since unsubscribed")
    index(sample_install, scan(sample_install))
    assert sorted(p.name for p in cache.iterdir()) == kept
