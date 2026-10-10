"""Empires: the empire file, bindings, which mods each needs, and hiding them."""

import os
from pathlib import Path

import pytest

from cold_steel.core.empires import (
    EmpireBook,
    bound_empires,
    build_catalog,
    changed_since,
    check_empire,
    empire_needs,
    empires_to_hide,
    fingerprints,
    hidden_names,
    hide_empires,
    playset_mods,
    read_hidden,
    remove_empires,
    restore_empires,
    shown_for,
    suggest_empires,
    unbound_empires,
)
from cold_steel.core.index import GAME, Indexer
from cold_steel.core.jobs import JobContext
from cold_steel.core.library import Scanner
from cold_steel.paradox.backup import backups_of
from cold_steel.paradox.empires import (
    EmpireReader,
    empire_file,
    join_empires,
    parse_empires,
    read_empire_file,
    split_empires,
)
from cold_steel.store.playsets import Playset, PlaysetEntry
from conftest import SampleInstall, add_empire_definitions, write_empires
from conftest import empire_block as block

ALPHA = "workshop:2000000001"
BETA = "workshop:2000000002"


ELVES, HUMANS = block("Divine Elven Order"), block("United Nations of Earth", "pc_continental")


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


# Bindings


def test_an_empire_can_belong_to_several_playsets(tmp_path: Path) -> None:
    path = tmp_path / "empires.json"
    book = EmpireBook.open(path)
    book.bind(["Elves"], "a")
    book.bind(["Elves"], "b")
    book.bind(["Elves"], "a")  # once each
    assert EmpireBook.open(path).playsets_of("Elves") == ("a", "b")
    assert book.count("a") == 1

    book.unbind(["Elves"], "a")
    assert book.playsets_of("Elves") == ("b",)
    book.unbind(["Elves"], "b")
    binding = book.get("Elves")
    assert binding is not None and binding.playsets == ()  # unbound by choice: kept


def test_a_deleted_playset_loses_its_bindings(tmp_path: Path) -> None:
    book = EmpireBook.open(tmp_path / "empires.json")
    book.bind(["Elves", "Humans"], "a")
    book.bind(["Humans"], "b")
    assert book.forget_playset("a") == 2
    assert book.get("Elves") is None  # bound to nothing else: unbound, may be suggested
    assert book.playsets_of("Humans") == ("b",)


def test_a_copy_of_a_playset_gets_its_empires(tmp_path: Path) -> None:
    book = EmpireBook.open(tmp_path / "empires.json")
    book.bind(["Elves"], "a")
    book.copy_playset("a", "copy")
    assert book.playsets_of("Elves") == ("a", "copy")


def test_an_unreadable_bindings_file_is_kept_aside(tmp_path: Path) -> None:
    path = tmp_path / "empires.json"
    path.write_text("{ not json")
    book = EmpireBook.open(path)
    assert book.problems and not path.exists()
    assert len(list(tmp_path.glob("empires.unreadable-*.json"))) == 1


def test_which_empires_show_and_which_are_hidden(tmp_path: Path) -> None:
    book = EmpireBook.open(tmp_path / "empires.json")
    names = ["Mine", "Theirs", "Shared", "Loose", "Orphan"]
    book.bind(["Mine", "Shared"], "a")
    book.bind(["Theirs", "Shared"], "b")
    book.bind(["Orphan"], "gone")  # a playset that no longer exists
    ids = {"a", "b"}
    assert bound_empires(book, names, {"a"}) == ["Mine", "Shared"]
    assert unbound_empires(book, names, ids) == ["Loose", "Orphan"]
    assert empires_to_hide(book, names, {"a"}, ids) == ["Theirs"]


def test_a_built_playset_shows_the_empires_of_its_source() -> None:
    built = Playset(
        id="built",
        name="Main (built)",
        entries=(PlaysetEntry("local:cold_steel_build_main", True, "Cold Steel build: Main"),),
    )
    assert shown_for(built) == {"built", "main"}


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


def test_unbound_empires_are_suggested_for_each_playset_with_their_mods(
    tmp_path: Path,
) -> None:
    from cold_steel.core.empires import Need

    book = EmpireBook.open(tmp_path / "empires.json")
    continental = (Need(("common/planet_classes", "pc_continental"), (ALPHA, BETA)),)
    nowhere = (Need(("common/planet_classes", "pc_nowhere"), ()),)
    empires = [
        ("Humans", continental),
        ("Vanilla", ()),
        ("Odd", nowhere),
        ("Chosen", continental),
        ("Moved", continental),
    ]
    book.bind(["Chosen"], "a")
    book.unbind(["Chosen"], "a")  # unbound by choice
    book.bind(["Moved"], "c")
    playsets = {"a": frozenset({ALPHA}), "b": frozenset({BETA}), "c": frozenset()}
    assert suggest_empires(empires, book, playsets) == {"Humans": ("a", "b")}


# Binding by Play


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


# Hiding


def test_hidden_empires_are_kept_byte_for_byte_and_put_back(tmp_path: Path) -> None:
    path = write_empires(tmp_path, ELVES, HUMANS)
    record, backups = tmp_path / "data/hidden_empires.txt", tmp_path / "backups"

    assert hide_empires({"Divine Elven Order"}, path, record, backups) == ("Divine Elven Order",)
    assert path.read_bytes() == HUMANS
    assert read_hidden(record).texts == (("Divine Elven Order", ELVES),)
    assert read_hidden(record).path == path
    (backup,) = backups_of(path, backups)
    assert backup.read_bytes() == ELVES + HUMANS

    # The game changed one empire and made another: both stay as it left them.
    changed, made = block("United Nations of Earth", "pc_arid"), block("New")
    path.write_bytes(changed + made)
    result = restore_empires(record, backups)
    assert result.back == ("Divine Elven Order",) and result.stuck == ()
    assert path.read_bytes() == changed + made + ELVES
    assert not record.exists()


def test_a_hidden_empire_never_overwrites_one_of_the_same_name(tmp_path: Path) -> None:
    path = write_empires(tmp_path, ELVES, HUMANS)
    record, backups = tmp_path / "hidden_empires.txt", tmp_path / "backups"
    hide_empires({"Divine Elven Order", "United Nations of Earth"}, path, record, backups)
    remade = block("Divine Elven Order", "pc_arctic")
    path.write_bytes(remade + HUMANS)  # remade with the same name; HUMANS put back by hand

    result = restore_empires(record, backups)
    assert result.back == ("United Nations of Earth",)  # the same bytes: already back
    assert result.stuck == ("Divine Elven Order",)
    assert path.read_bytes() == remade + HUMANS
    assert hidden_names(record) == ("Divine Elven Order",)
    # Hiding again leaves alone the one still kept.
    assert hide_empires({"Divine Elven Order"}, path, record, backups) == ()
    assert path.read_bytes() == remade + HUMANS


def test_empires_come_back_into_a_file_the_game_deleted(tmp_path: Path) -> None:
    path = write_empires(tmp_path, ELVES, HUMANS)
    record, backups = tmp_path / "hidden_empires.txt", tmp_path / "backups"
    hide_empires({"Divine Elven Order"}, path, record, backups)
    path.unlink()  # its last empire was deleted in the game
    assert restore_empires(record, backups).back == ("Divine Elven Order",)
    assert path.read_bytes() == ELVES


def test_a_failed_write_hides_nothing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import cold_steel.core.empires as empires

    path = write_empires(tmp_path, ELVES, HUMANS)
    record, backups = tmp_path / "hidden_empires.txt", tmp_path / "backups"

    def fail(*args: object) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(empires, "write_empire_file", fail)
    with pytest.raises(OSError):
        hide_empires({"Divine Elven Order"}, path, record, backups)
    assert path.read_bytes() == ELVES + HUMANS
    assert not record.exists()


def test_deleting_an_empire_backs_up_first(tmp_path: Path) -> None:
    path = write_empires(tmp_path, ELVES, HUMANS)
    mode = path.stat().st_mode
    assert remove_empires({"Divine Elven Order", "Gone"}, path, tmp_path / "b") == (
        "Divine Elven Order",
    )
    assert path.read_bytes() == HUMANS
    assert path.stat().st_mode == mode
    assert len(backups_of(path, tmp_path / "b")) == 1
    assert not any(p.name.endswith(".tmp") for p in os.scandir(tmp_path))
