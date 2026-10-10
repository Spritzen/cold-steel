"""Empires: the empire file, each playset's list, which mods each empire needs,
and handing a list to the game."""

from pathlib import Path

import pytest

from cold_steel.core.empires import (
    EmpireLists,
    FormatError,
    build_catalog,
    changed_since,
    check_empire,
    empire_needs,
    fingerprints,
    first_lists,
    in_game,
    list_owner,
    playset_mods,
    put_in_game,
    shelve_outside_changes,
    take_back,
)
from cold_steel.core.index import GAME, Indexer
from cold_steel.core.jobs import JobContext
from cold_steel.core.library import Scanner
from cold_steel.paradox.backup import backups_of
from cold_steel.paradox.empires import (
    Empire,
    EmpireReader,
    empire_file,
    file_format,
    join_empires,
    parse_empires,
    read_empire_file,
    split_empires,
)
from cold_steel.store.empires import LOOSE, load_state
from cold_steel.store.playsets import Playset, PlaysetEntry
from conftest import SampleInstall, add_empire_definitions, write_empires
from conftest import empire_block as block

ALPHA = "workshop:2000000001"
BETA = "workshop:2000000002"
ELVES, HUMANS = block("Divine Elven Order"), block("United Nations of Earth", "pc_continental")
BUILT = Playset(
    id="built",
    name="Main (built)",
    entries=(PlaysetEntry("local:cold_steel_build_main", True, "Cold Steel build: Main"),),
)


def empires(*blocks: bytes) -> list[Empire]:
    """Empire records, as read from a file holding these blocks."""
    return list(parse_empires(Path("x"), b"".join(blocks)).empires)


# The empire file


def test_splitting_keeps_every_byte() -> None:
    data = b"# a note\r\n" + ELVES + HUMANS
    head, blocks = split_empires(data)
    assert head == b"# a note\r\n"
    assert [name for name, _ in blocks] == ["Divine Elven Order", "United Nations of Earth"]
    assert blocks[0][1] == ELVES
    assert join_empires(head, [t for _, t in blocks]) == data


def test_joining_adds_a_line_break_a_last_block_lacks() -> None:
    assert join_empires(b"", [ELVES.rstrip(), HUMANS]) == ELVES + HUMANS
    assert join_empires(ELVES.rstrip(), [HUMANS]) == ELVES + HUMANS


def test_reading_an_empire() -> None:
    (empire,) = parse_empires(Path("x"), ELVES).empires
    info = empire.info
    assert info is not None
    assert (info.species, info.authority, info.origin) == (
        "Elf",
        "auth_imperial",
        "origin_life_seeded",
    )
    assert info.ethics == ("ethic_militarist", "ethic_fanatic_spiritualist")
    assert info.civics == ("civic_ascensionists", "civic_chosen")
    assert info.uses == (
        ("common/species_classes", "HUM"),
        ("common/name_lists", "HUMAN3"),
        ("common/traits", "trait_venerable"),
        ("common/governments/authorities", "auth_imperial"),
        ("common/governments", "gov_theocratic_monarchy"),
        ("common/planet_classes", "pc_desert"),
        ("common/graphical_culture", "humanoid_01"),
        ("common/traits", "leader_trait_spark_of_genius"),
        ("common/ethics", "ethic_militarist"),
        ("common/ethics", "ethic_fanatic_spiritualist"),
        ("common/governments/civics", "civic_ascensionists"),
        ("common/governments/civics", "civic_chosen"),
        ("common/governments/civics", "origin_life_seeded"),
    )


def test_a_broken_block_is_kept_but_not_read() -> None:
    broken = b'"Broken"=\r\n{\r\n\tkey="Broken\r\n}\r\n'
    file = parse_empires(Path("x"), ELVES + broken)
    assert file.names == ("Divine Elven Order", "Broken")
    assert file.empires[1].info is None and file.empires[1].problem
    assert file.empires[1].text == broken


def test_the_newest_empire_file_is_used(tmp_path: Path) -> None:
    assert empire_file(tmp_path) is None
    for name in ("user_empire_designs_v3.4.txt", "user_empire_designs_v3.10.txt"):
        (tmp_path / name).write_bytes(ELVES)
    found = empire_file(tmp_path)
    assert found is not None and found.name == "user_empire_designs_v3.10.txt"


def test_the_file_is_read_again_only_once_it_changed(tmp_path: Path) -> None:
    path = write_empires(tmp_path, ELVES)
    reader = EmpireReader()
    first = reader.read(path)
    assert reader.read(path) is first
    path.write_bytes(ELVES + HUMANS)
    assert reader.read(path).names == ("Divine Elven Order", "United Nations of Earth")


# Lists


def test_a_built_playset_uses_its_sources_list() -> None:
    assert list_owner(BUILT) == "main"
    assert list_owner(Playset(id="main", name="Main")) == "main"


def test_adding_replacing_and_skipping_in_a_list(tmp_path: Path) -> None:
    lists = EmpireLists(tmp_path / "empires", tmp_path / "backups")
    assert lists.read("a").empires == ()
    first = lists.add("a", empires(ELVES, HUMANS), replace=False)
    assert first.added == ("Divine Elven Order", "United Nations of Earth")
    assert lists.path("a").read_bytes() == ELVES + HUMANS

    remade = block("Divine Elven Order", "pc_arid")
    skipped = lists.add("a", empires(remade, block("New")), replace=False)
    assert skipped.skipped == ("Divine Elven Order",) and skipped.added == ("New",)
    replaced = lists.add("a", empires(remade), replace=True)
    assert replaced.replaced == ("Divine Elven Order",)
    # Replaced in its place; every block byte for byte.
    assert lists.path("a").read_bytes() == remade + HUMANS + block("New")
    assert backups_of(lists.path("a"), lists.backup_dir)  # backed up before each write

    assert lists.remove("a", {"United Nations of Earth", "Gone"}) == ("United Nations of Earth",)
    assert lists.read("a").names == ("Divine Elven Order", "New")
    lists.copy_list("a", "copy")
    assert lists.path("copy").read_bytes() == lists.path("a").read_bytes()
    assert lists.owners() == ["a", "copy"]


# Handing a list to the game


@pytest.fixture
def lists(tmp_path: Path) -> EmpireLists:
    found = EmpireLists(tmp_path / "data/empires", tmp_path / "data/backups")
    found.root.mkdir(parents=True)
    return found


def test_play_gives_the_game_exactly_the_playsets_list(lists: EmpireLists, tmp_path: Path) -> None:
    game = write_empires(tmp_path, ELVES, HUMANS)
    state = tmp_path / "data/empires_in_game.json"
    lists.add("a", empires(ELVES), replace=False)
    lists.add("b", empires(HUMANS), replace=False)
    shelve_outside_changes(lists, game, state)  # the file as Cold Steel last saw it

    put_in_game(lists, "a", game, state, tmp_path / "backups")
    assert game.read_bytes() == ELVES
    assert in_game(state) == "a"
    (backup,) = backups_of(game, tmp_path / "backups")
    assert backup.read_bytes() == ELVES + HUMANS

    # In the game: the Elves are changed, and an empire is made.
    changed, made = block("Divine Elven Order", "pc_arid"), block("Made in Game")
    game.write_bytes(made + changed)
    result = take_back(lists, state, lambda _: True)
    assert result is not None and result.new == ("Made in Game", "Divine Elven Order")
    assert lists.path("a").read_bytes() == made + changed  # byte for byte, as the game left it
    assert lists.read("b").names == ("United Nations of Earth",)  # untouched
    assert in_game(state) == ""
    assert take_back(lists, state, lambda _: True) is None  # nothing left in the game


def test_an_empty_list_gives_the_game_an_empty_file(lists: EmpireLists, tmp_path: Path) -> None:
    game = write_empires(tmp_path, ELVES)
    state = tmp_path / "data/empires_in_game.json"
    put_in_game(lists, "empty", game, state, tmp_path / "backups")
    assert game.read_bytes() == b""
    # The Elves were in no list: kept under Not in any playset first.
    assert lists.read(LOOSE).names == ("Divine Elven Order",)


def test_a_deleted_playsets_empires_go_to_the_loose_list(
    lists: EmpireLists, tmp_path: Path
) -> None:
    game = write_empires(tmp_path)
    state = tmp_path / "data/empires_in_game.json"
    lists.add("a", empires(ELVES), replace=False)
    put_in_game(lists, "a", game, state, tmp_path / "backups")
    lists.path("a").unlink()  # the playset was deleted while the game ran
    result = take_back(lists, state, lambda _: False)
    assert result is not None and result.loose
    assert lists.read(LOOSE).names == ("Divine Elven Order",)
    assert not lists.path("a").exists()


def test_empires_changed_outside_cold_steel_go_to_the_loose_list(
    lists: EmpireLists, tmp_path: Path
) -> None:
    game = write_empires(tmp_path, ELVES)
    state = tmp_path / "data/empires_in_game.json"
    lists.add("a", empires(ELVES), replace=False)
    assert shelve_outside_changes(lists, game, state) == ()  # all in a list already
    assert load_state(state).digest  # the file as it was seen

    # The game was started from the launcher, and an empire made.
    game.write_bytes(ELVES.replace(b"\t", b"  ") + HUMANS)
    assert shelve_outside_changes(lists, game, state) == ("United Nations of Earth",)
    assert lists.read(LOOSE).names == ("United Nations of Earth",)
    assert shelve_outside_changes(lists, game, state) == ()  # once


def test_a_failed_write_leaves_the_game_file_and_list_alone(
    lists: EmpireLists, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import cold_steel.core.empires as core

    game = write_empires(tmp_path, ELVES)
    state = tmp_path / "data/empires_in_game.json"
    lists.add("a", empires(ELVES), replace=False)

    def fail(*args: object) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(core, "write_empire_file", fail)
    with pytest.raises(OSError):
        put_in_game(lists, "a", game, state, tmp_path / "backups")
    assert game.read_bytes() == ELVES
    assert in_game(state) == ""  # nothing to take back


def test_the_first_lists_come_from_earlier_bindings(tmp_path: Path) -> None:
    lists = EmpireLists(tmp_path / "data/empires", tmp_path / "data/backups")
    game = write_empires(tmp_path, ELVES, HUMANS, block("Korinth"))
    state = tmp_path / "data/empires_in_game.json"
    old = tmp_path / "data/empires.json"
    old.parent.mkdir()
    old.write_text(
        '{"version": 1, "empires": {'
        '"Divine Elven Order": {"playsets": ["main", "second"]},'
        '"Korinth": {"playsets": ["built"]},'
        '"Gone": {"playsets": ["main"]}}}'
    )
    owners = {"main": "main", "second": "second", "built": "main"}
    assert first_lists(lists, game, state, old, owners)
    assert lists.read("main").names == ("Divine Elven Order", "Korinth")
    assert lists.read("second").names == ("Divine Elven Order",)
    assert lists.read(LOOSE).names == ("United Nations of Earth",)
    assert not old.exists() and old.with_name("empires.json.old").exists()
    assert not first_lists(lists, game, state, old, owners)  # once
    assert shelve_outside_changes(lists, game, state) == ()  # the file is known


# Which mods an empire needs


@pytest.fixture
def catalog_install(sample_install: SampleInstall) -> SampleInstall:
    """The game defines the desert planet class and every other thing the
    empires use. Alpha adds a continental one; Beta adds it too."""
    install = sample_install.root / "games/steamapps/common/Stellaris"
    mods = {
        sample_install.workshop_dir / "2000000001/common/planet_classes/alpha.txt": (
            b"pc_continental = { }\n"
        ),
        sample_install.workshop_dir / "2000000002/common/planet_classes/beta.txt": (
            b"pc_continental = { }\n"
        ),
        sample_install.workshop_dir / "2000000002/common/species_classes/beta.txt": (
            b"BETA_CLASS = { }\n"
        ),
    }
    add_empire_definitions(install)
    for path, data in mods.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    return sample_install


def test_an_empire_needs_the_mods_that_define_what_it_uses(
    catalog_install: SampleInstall, tmp_path: Path
) -> None:
    library = Scanner(*catalog_install.scanner_args())(JobContext())
    index = Indexer(library, tmp_path / "index")(JobContext())
    catalog = build_catalog(index)
    assert catalog.defined[("common/planet_classes", "pc_continental")] == {ALPHA, BETA}
    # Species classes are indexed as whole files, so they're read for the catalog.
    assert catalog.defined[("common/species_classes", "BETA_CLASS")] == {BETA}
    assert GAME in catalog.defined[("common/species_classes", "HUM")]

    elves = parse_empires(Path("x"), ELVES).empires[0].info
    humans = parse_empires(Path("x"), HUMANS).empires[0].info
    odd = parse_empires(Path("x"), block("Odd", "pc_nowhere")).empires[0].info
    assert elves is not None and humans is not None and odd is not None
    assert empire_needs(elves, catalog) == ()  # all the game's
    (need,) = empire_needs(humans, catalog)
    assert need.use == ("common/planet_classes", "pc_continental") and need.mods == (ALPHA, BETA)
    (nowhere,) = empire_needs(odd, catalog)
    assert nowhere.mods == ()  # in no installed mod

    # Any one of its mods will do.
    assert check_empire([need], {BETA}).ok
    assert check_empire([need], {"workshop:9"}).missing == (need,)
    assert not check_empire([nowhere], {ALPHA, BETA}).ok

    # Catalogued again, an unchanged mod isn't read again.
    again = build_catalog(index, catalog)
    assert again.layers[ALPHA] is catalog.layers[ALPHA]


def test_a_build_counts_as_every_mod_inside_it(tmp_path: Path) -> None:
    (tmp_path / "main.json").write_text(f'{{"built": "x", "order": ["{ALPHA}", "{BETA}@snap"]}}')
    built = Playset(
        id="built",
        name="Main (built)",
        entries=(PlaysetEntry("local:cold_steel_build_main", True, "Cold Steel build: Main"),),
    )
    assert playset_mods(built, tmp_path) == {"local:cold_steel_build_main", ALPHA, BETA}


# Empire file formats


def test_a_files_format_comes_from_its_name() -> None:
    assert file_format(Path("user_empire_designs_v3.4.txt")) == "3.4"
    assert file_format(Path("user_empire_designs_v3.10.txt")) == "3.10"
    assert file_format(Path("loose.txt")) == ""


def test_each_list_records_its_format_and_the_game_that_played_it(
    lists: EmpireLists, tmp_path: Path
) -> None:
    game = write_empires(tmp_path, ELVES)
    state = tmp_path / "data/empires_in_game.json"
    shelve_outside_changes(lists, game, state)
    assert lists.info(LOOSE).format == "3.4"  # found in the game's v3.4 file
    lists.add("a", empires(ELVES), replace=False, format="3.4")
    put_in_game(lists, "a", game, state, tmp_path / "backups")
    take_back(lists, state, lambda _: True, "v4.5.2")
    assert (lists.info("a").format, lists.info("a").played) == ("3.4", "v4.5.2")
    lists.copy_list("a", "copy")
    assert lists.info("copy").format == "3.4"


def test_empires_in_another_format_dont_mix_into_a_list(lists: EmpireLists) -> None:
    lists.add("a", empires(ELVES), replace=False, format="3.4")
    with pytest.raises(FormatError):
        lists.add("a", empires(HUMANS), replace=False, format="3.5")
    assert lists.read("a").names == ("Divine Elven Order",)
    lists.add("b", empires(HUMANS), replace=False, format="3.5")  # an empty list takes any
    assert lists.info("b").format == "3.5"


def test_play_wont_give_the_game_a_list_in_an_older_format(
    lists: EmpireLists, tmp_path: Path
) -> None:
    old = write_empires(tmp_path, ELVES)
    state = tmp_path / "data/empires_in_game.json"
    lists.add("a", empires(ELVES), replace=False, format="3.4")
    new = tmp_path / "user_empire_designs_v3.5.txt"  # a game update started a new file
    new.write_bytes(HUMANS)
    assert empire_file(tmp_path) == new
    with pytest.raises(FormatError, match="until they're converted"):
        put_in_game(lists, "a", new, state, tmp_path / "backups")
    assert new.read_bytes() == HUMANS and old.read_bytes() == ELVES
    assert in_game(state) == ""
    put_in_game(lists, "empty", new, state, tmp_path / "backups")  # nothing to convert


# What changed in game


def test_empires_new_or_changed_since_play(tmp_path: Path) -> None:
    path = write_empires(tmp_path, ELVES, HUMANS)
    before = fingerprints(read_empire_file(path))
    respaced = ELVES.replace(b"\t", b"    ")  # the same empire, laid out differently
    changed = block("United Nations of Earth", "pc_arid")
    path.write_bytes(respaced + changed + block("Made in game"))
    assert changed_since(before, read_empire_file(path)) == [
        "United Nations of Earth",
        "Made in game",
    ]
