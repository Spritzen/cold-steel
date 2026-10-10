"""The empire checker, each empire's sources, and converting a list to a new
empire file format (Phase 10)."""

from pathlib import Path

import pytest

from cold_steel.core.definitions import digest
from cold_steel.core.empire_check import (
    BROKEN,
    GONE,
    LACKING,
    ModInfo,
    PlaysetMods,
    check_lists,
    installed_mods,
    older_game,
    pending,
    sources_for,
)
from cold_steel.core.empire_conversions import (
    Conversion,
    Rename,
    conversion_steps,
    convert_block,
    convert_list,
    load_conversions,
    plan_conversion,
)
from cold_steel.core.empires import Catalog, EmpireLists, build_catalog
from cold_steel.core.index import Indexer
from cold_steel.core.jobs import JobContext
from cold_steel.core.library import Library, Scanner
from cold_steel.core.mods import Mod
from cold_steel.paradox.empires import Empire, parse_empires
from cold_steel.store.empires import LOOSE, Source, Sources, UseSources
from conftest import SampleInstall
from conftest import empire_block as block

ALPHA = "workshop:2000000001"  # Alpha Interface 1.2: adds pc_continental
BETA = "workshop:2000000002"  # Beta Ships 0.9: adds pc_continental and BETA_CLASS
ELVES, HUMANS = block("Divine Elven Order"), block("United Nations of Earth", "pc_continental")
ODD = block("Odd", "pc_gone")  # a planet class no installed mod has
PD = "workshop:1199002146"


def empires(*blocks: bytes) -> list[Empire]:
    return list(parse_empires(Path("x"), b"".join(blocks)).empires)


def gone_with(mod: str, name: str, version: str, *, later: bool = False) -> Sources:
    """Odd's sources: pc_gone came from this mod."""
    use = UseSources("common/planet_classes", "pc_gone", (Source(mod, name, version),))
    return Sources(digest(ODD), (use,), later=later)


@pytest.fixture
def scanned(catalog_install: SampleInstall, tmp_path: Path) -> tuple[Library, Catalog]:
    library = Scanner(*catalog_install.scanner_args())(JobContext())
    return library, build_catalog(Indexer(library, tmp_path / "index")(JobContext()))


@pytest.fixture
def lists(tmp_path: Path) -> EmpireLists:
    return EmpireLists(tmp_path / "data/empires", tmp_path / "data/backups")


def test_the_checker_says_what_each_empire_lacks_and_why(
    scanned: tuple[Library, Catalog], lists: EmpireLists
) -> None:
    library, catalog = scanned
    installed = installed_mods(library.every_mod)
    lists.add("main", empires(ELVES, HUMANS, ODD), replace=False, format="3.4")
    lists.set_sources("main", {"Odd": gone_with(BETA, "Beta Ships", "0.8")})
    users = {
        "main": [
            PlaysetMods("Main", frozenset({ALPHA})),
            PlaysetMods("Main (built)", frozenset()),
        ]
    }
    report = check_lists(lists, users, catalog, installed, "v4.5.2", "3.4")

    found = report.get("main")
    assert found is not None and found.count == 3 and not found.problem
    by_name = {e.name: e.problems for e in found.empires}
    assert "Divine Elven Order" not in by_name  # all the game's
    (lacking,) = by_name["United Nations of Earth"]
    assert lacking.kind == LACKING
    assert lacking.text == (
        "Its planet class pc_continental is only in Alpha Interface or Beta Ships, "
        "which Main (built) lacks."
    )
    (gone,) = by_name["Odd"]
    assert gone.kind == GONE
    assert gone.text == "Made with Beta Ships 0.8. Its planet class pc_gone is gone in 0.9."
    assert report.marked == 2

    # The United Nations had no sources yet: the check found them, marked found later.
    une = found.sources["United Nations of Earth"]
    assert une is not None and une.later
    assert [m.mod for m in une.uses[0].mods] == [ALPHA, BETA]
    assert "Odd" not in found.sources  # its sources stand


def test_a_source_mod_thats_gone_or_found_later_is_worded_so(
    scanned: tuple[Library, Catalog], lists: EmpireLists
) -> None:
    library, catalog = scanned
    installed = installed_mods(library.every_mod)
    lists.add("main", empires(ODD), replace=False)

    lists.set_sources("main", {"Odd": gone_with(PD, "Planetary Diversity", "3.2")})
    (found,) = check_lists(lists, {}, catalog, installed, "", "").get("main").empires  # type: ignore[union-attr]
    assert found.problems[0].text == (
        "Its planet class pc_gone isn't in the game or any installed mod. "
        "Made with Planetary Diversity 3.2, which isn't installed now."
    )

    lists.set_sources("main", {"Odd": gone_with(BETA, "Beta Ships", "0.9", later=True)})
    (found,) = check_lists(lists, {}, catalog, installed, "", "").get("main").empires  # type: ignore[union-attr]
    assert found.problems[0].text == (
        "Found later with Beta Ships 0.9. Its planet class pc_gone is gone from it now."
    )

    lists.set_sources("main", {"Odd": None})
    (found,) = check_lists(lists, {}, catalog, installed, "", "").get("main").empires  # type: ignore[union-attr]
    assert found.problems[0].text == (
        "Its planet class pc_gone isn't in the game or any installed mod."
    )


def test_the_loose_list_and_broken_blocks_are_checked_too(
    scanned: tuple[Library, Catalog], lists: EmpireLists
) -> None:
    library, catalog = scanned
    broken = b'"Broken"=\n{\n\tkey="Broken"\n\tspecies={ class=\n}\n'
    lists.add(LOOSE, empires(HUMANS, ODD), replace=False)
    lists.write("main", broken)
    report = check_lists(lists, {}, catalog, installed_mods(library.every_mod), "", "")

    loose = report.get(LOOSE)
    assert loose is not None
    # No playset plays the loose list, so only what no mod has is marked.
    assert [(e.name, e.problems[0].kind) for e in loose.empires] == [("Odd", GONE)]
    main = report.get("main")
    assert main is not None and main.empires[0].problems[0].kind == BROKEN


def test_a_pending_or_changed_empire_gets_its_sources_again(
    scanned: tuple[Library, Catalog], lists: EmpireLists
) -> None:
    library, catalog = scanned
    installed = installed_mods(library.every_mod)
    lists.add("main", empires(HUMANS, ELVES), replace=False)
    lists.set_sources(
        "main",
        {
            "United Nations of Earth": pending(HUMANS),
            "Divine Elven Order": Sources(123),  # recorded for another block
            "Gone Empire": Sources(1),  # no longer in the list
        },
    )
    found = check_lists(lists, {}, catalog, installed, "", "").get("main")
    assert found is not None
    une, elves = found.sources["United Nations of Earth"], found.sources["Divine Elven Order"]
    assert une is not None and not une.later and not une.pending  # entered, then found
    assert elves is not None and elves.later and elves.uses == ()  # needs no mods
    assert found.sources["Gone Empire"] is None  # forgotten


def test_sources_for_empires_entering_a_list(scanned: tuple[Library, Catalog]) -> None:
    library, catalog = scanned
    installed = installed_mods(library.every_mod)
    une, odd = empires(HUMANS, ODD)
    known = {"Odd": gone_with(BETA, "Beta Ships", "0.8")}

    found = sources_for([une, odd], catalog, installed, known)
    assert found["Odd"] == known["Odd"]  # the same block: kept from the list it came from
    made = found["United Nations of Earth"]
    assert made is not None and not made.later
    assert {(m.name, m.version) for m in made.uses[0].mods} == {
        ("Alpha Interface", "1.2"),
        ("Beta Ships", "0.9"),
    }
    # Before the mods are known, they're recorded at the next check.
    assert sources_for([une], None, installed)["United Nations of Earth"] == pending(HUMANS)


def test_a_pinned_copy_counts_as_its_workshop_mod() -> None:
    steam = Mod(key=ALPHA, source="workshop", name="Alpha", version="1.3")
    pinned = Mod(key=f"{ALPHA}@snap", source="workshop", name="Alpha", version="1.2")
    assert installed_mods([steam, pinned])[ALPHA] == ModInfo("Alpha", "1.3")
    assert installed_mods([pinned])[ALPHA] == ModInfo("Alpha", "1.2")


def test_a_mod_without_a_version_is_named_by_its_update(
    scanned: tuple[Library, Catalog], lists: EmpireLists
) -> None:
    _, catalog = scanned
    lists.add("main", empires(ODD), replace=False)
    use = UseSources(
        "common/planet_classes", "pc_gone", (Source(PD, "Planetary Diversity", "", "2026-06-20"),)
    )
    lists.set_sources("main", {"Odd": Sources(digest(ODD), (use,))})
    installed = {PD: ModInfo("Planetary Diversity", "", "2026-09-22")}
    (found,) = check_lists(lists, {}, catalog, installed, "", "").get("main").empires  # type: ignore[union-attr]
    assert found.problems[0].text == (
        "Made with Planetary Diversity, updated 2026-06-20. "
        "Its planet class pc_gone is gone in its update of 2026-09-22."
    )


def test_each_mods_update_is_the_date_of_its_newest_file(
    catalog_install: SampleInstall, tmp_path: Path
) -> None:
    import os

    alpha = catalog_install.workshop_dir / "2000000001/descriptor.mod"
    for path in alpha.parent.rglob("*"):
        os.utime(path, (1_750_000_000, 1_750_000_000))  # 2025-06-15
    os.utime(alpha, (1_758_000_000, 1_758_000_000))  # 2025-09-16, the newest
    library = Scanner(*catalog_install.scanner_args())(JobContext())
    index = Indexer(library, tmp_path / "index")(JobContext())
    found = installed_mods(library.every_mod, index)[ALPHA]
    assert (found.name, found.version, found.updated) == ("Alpha Interface", "1.2", "2025-09-16")


def test_an_older_format_and_an_older_game_are_reported(
    scanned: tuple[Library, Catalog], lists: EmpireLists
) -> None:
    library, catalog = scanned
    installed = installed_mods(library.every_mod)
    lists.add("main", empires(ELVES), replace=False, format="3.4")
    lists.set_info("main", played="v4.4.1")

    found = check_lists(lists, {}, catalog, installed, "v4.5.2", "3.5").get("main")
    assert found is not None and not found.convertible
    assert found.problem == (
        "Its empires are in empire file 3.4's format, and the game uses 3.5's. "
        "No conversion is known: rebuild them in the game's empire creator."
    )
    assert found.marked == 1  # every empire in it
    assert "last played by the game v4.4.1, and the game is v4.5.2 now" in found.played

    table = [Conversion("3.4", "3.5")]
    found = check_lists(lists, {}, catalog, installed, "v4.5.2", "3.5", table).get("main")
    assert found is not None and found.convertible and "Convert…" in found.problem

    found = check_lists(lists, {}, catalog, installed, "v4.4.9", "3.4").get("main")
    assert found is not None and not found.problem and not found.played and not found.marked


def test_older_games_by_major_and_minor_version() -> None:
    assert older_game("v4.4.1", "v4.5.2")
    assert not older_game("v4.5.0", "v4.5.2")  # a patch isn't a new game
    assert not older_game("", "v4.5.2")
    assert not older_game("v5.0", "v4.9.9")


# Converting to a new format


def test_conversions_chain_from_one_format_to_the_next() -> None:
    table = [Conversion("3.4", "3.5"), Conversion("3.5", "3.6"), Conversion("2.0", "3.0")]
    assert conversion_steps(table, "3.4", "3.6") == (table[0], table[1])
    assert conversion_steps(table, "3.4", "3.4") == ()
    assert conversion_steps(table, "3.6", "3.4") is None
    assert load_conversions() == ()  # no format change seen yet
    assert load_conversions(b'{"conversions": [{"from": "3.4", "to": "3.5"}]}') == (
        Conversion("3.4", "3.5"),
    )


def test_a_conversion_renames_only_what_its_table_says() -> None:
    renames = (
        Rename("planet_class", "pc_desert", "pc_arid"),
        Rename("trait", "trait_venerable", "trait_very_venerable"),
        Rename("city_graphical_culture", to_field="city_look"),
    )
    step = Conversion("3.4", "3.5", renames)
    text = block("Elves") + b'\tplanet_class_2="pc_desert"\r\n\tx=pc_desert_two\r\n'
    out = convert_block(text, [step])
    assert b'\tplanet_class="pc_arid"\r\n' in out
    assert b'\t\ttrait="trait_very_venerable"\r\n' in out  # nested, at any depth
    assert b'\tcity_look="humanoid_01"\r\n' in out
    assert b'planet_class_2="pc_desert"' in out and b"x=pc_desert_two" in out  # untouched
    assert len(out.splitlines()) == len(text.splitlines())


def test_converting_a_list_shows_every_change_and_keeps_a_version(lists: EmpireLists) -> None:
    lists.add("main", empires(ELVES, HUMANS), replace=False, format="3.4")
    table = [Conversion("3.4", "3.5", (Rename("planet_class", "pc_continental", "pc_wet"),))]
    file = lists.read("main")
    assert plan_conversion(file, "3.4", "3.6", table) is None

    plan = plan_conversion(file, "3.4", "3.5", table)
    assert plan is not None
    (changed,) = plan.changed
    assert changed.name == "United Nations of Earth"
    assert changed.lines == (('planet_class="pc_continental"', 'planet_class="pc_wet"'),)

    convert_list(lists, "main", plan)
    assert lists.info("main").format == "3.5"
    assert lists.read("main").names == ("Divine Elven Order", "United Nations of Earth")
    assert b'"pc_wet"' in lists.path("main").read_bytes()
    newest = lists.versions("main")[0]
    assert (newest.note, newest.format) == ("Before converting to empire file 3.5", "3.4")
    assert lists.read_version(newest).empires[1].text == HUMANS
