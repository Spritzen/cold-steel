"""Phase 5: choosing winners, and the patch mod that makes them win.

The main check: build the patch, rescan, then find the conflicts again with
the patch in the playset. The Phase 4 winner rules must then pick the patch's
version of every object the user chose for.
"""

import shutil
from collections.abc import Mapping
from pathlib import Path

import pytest

from cold_steel.core.conflicts import FILE, Conflict, ConflictFinder, Found
from cold_steel.core.deploy import withdraw
from cold_steel.core.index import GAME, Index
from cold_steel.core.jobs import JobContext
from cold_steel.core.library import Library
from cold_steel.core.patch import (
    PATCH_PREFIX,
    PatchError,
    check_own,
    patch_key,
    patch_keys,
    plan_patch,
    remove_patch,
    winning_name,
    with_patch_last,
    write_patch,
)
from cold_steel.core.play import plan_play
from cold_steel.core.resolve import CHOSEN, NONE, STALE, ResolutionBook, copy_resolutions
from cold_steel.store.playsets import Playset, PlaysetEntry
from cold_steel.store.resolutions import Ignore, resolutions_file
from conftest import SampleInstall
from test_conflicts import ALPHA, BETA, BOM, LOCAL, conflict, index, scan, write

PLAYSET = Playset(
    id="p", name="Test", entries=(PlaysetEntry(ALPHA), PlaysetEntry(BETA), PlaysetEntry(LOCAL))
)
PATCH = patch_key("p")


def find(install: SampleInstall, playset: Playset = PLAYSET) -> tuple[Library, Index, Found]:
    library = scan(install)
    idx = index(install, library)
    found = ConflictFinder(idx, playset, library, leave_out=frozenset({PATCH}))(JobContext())
    return library, idx, found


def book(tmp_path: Path) -> ResolutionBook:
    return ResolutionBook.open(resolutions_file("p", tmp_path / "resolutions"))


def claim_of(c: Conflict, layer: str) -> int:
    return next(n for n, claim in enumerate(c.claims) if claim.layer == layer)


def build(install: SampleInstall, tmp_path: Path, choices: ResolutionBook) -> Found:
    """Generate the patch, add it last, and find the conflicts with it included."""
    library, idx, found = find(install)
    plan = plan_patch(found, choices.resolutions, idx)
    assert plan.left_out == ()
    write_patch(plan, PLAYSET, library.game, tmp_path / "patches")
    playset = with_patch_last(PLAYSET)
    library = scan(install)
    assert PATCH in {m.key for m in library.mods}
    # Play loads it last.
    assert plan_play(playset, library).load.enabled_mods[-1] == f"mod/{PATCH_PREFIX}p.mod"
    return ConflictFinder(index(install, library), playset, library)(JobContext())


def text_of(found: Found, c: Conflict) -> bytes:
    win = c.winning
    data = found.index.read(win.layer, win.path)
    assert data is not None
    return data[win.definition.start : win.definition.end] if win.definition else data


EVENT = b"namespace = ev\ncountry_event = { id = ev.1 is_triggered_only = %s }\n"
SPRITE = b'spriteTypes = { spriteType = { name = "GFX_x" texturefile = "%s.dds" } }\n'
CLASHES: Mapping[str, Mapping[str, bytes]] = {
    ALPHA: {
        "common/technology/b_alpha.txt": b"tech_x = { cost = 1 }\n",
        "events/b_alpha.txt": EVENT % b"yes",
        "localisation/alpha_l_english.yml": BOM + b'l_english:\n tech_x:0 "Alpha"\n',
        "common/defines/b_alpha.txt": b"NGame = { START_YEAR = 2300 }\n",
        "interface/b_alpha.gfx": SPRITE % b"a",
        "gfx/models/thing.txt": b"alpha's whole file\n",
    },
    BETA: {
        "common/technology/a_beta.txt": b"tech_x = { cost = 2 }\n",
        "events/a_beta.txt": EVENT % b"no",
        "localisation/beta_l_english.yml": BOM + b'l_english:\n tech_x:0 "Beta"\n',
        "common/defines/a_beta.txt": b"NGame = { START_YEAR = 2400 }\n",
        "interface/a_beta.gfx": SPRITE % b"b",
        "gfx/models/thing.txt": b"beta's whole file\n",
    },
}


@pytest.mark.parametrize(
    ("kind", "key", "natural", "wanted"),
    [
        # Last wins: b_alpha.txt sorts last, so Alpha wins until Beta is chosen.
        ("common/technology", "tech_x", ALPHA, b"cost = 2"),
        # First wins: a_beta.txt sorts first.
        ("events", "ev.1", BETA, b"is_triggered_only = yes"),
        # Localisation outside replace/: the first file name wins.
        ("localisation", "tech_x", ALPHA, b'"Beta"'),
        ("common/defines", "NGame.START_YEAR", ALPHA, b"2400"),
        ("spritetype", "GFX_x", ALPHA, b"b.dds"),
        # A whole file: the mod loaded last replaces it.
        (FILE, "gfx/models/thing.txt", BETA, b"alpha's"),
    ],
)
def test_the_patch_makes_the_chosen_version_win(
    sample_install: SampleInstall,
    tmp_path: Path,
    kind: str,
    key: str,
    natural: str,
    wanted: bytes,
) -> None:
    write(sample_install, CLASHES)
    _, idx, found = find(sample_install)
    clash = conflict(found, kind, key)
    assert clash.winning.layer == natural
    loser = next(c for c in clash.claims if c.layer not in (natural, GAME))

    choices = book(tmp_path)
    choices.choose(clash, loser, idx)
    assert choices.state(clash, idx) == CHOSEN

    found = build(sample_install, tmp_path, choices)
    after = conflict(found, kind, key)
    assert after.winning.layer == PATCH
    assert wanted in text_of(found, after)


def test_every_choice_in_one_patch(sample_install: SampleInstall, tmp_path: Path) -> None:
    write(sample_install, CLASHES)
    _, idx, found = find(sample_install)
    choices = book(tmp_path)
    for clash in found.conflicts:
        if len(clash.mods) > 1:
            choices.choose(clash, clash.claims[claim_of(clash, BETA)], idx)

    after = build(sample_install, tmp_path, choices)
    for clash in after.conflicts:
        assert clash.winning.layer == PATCH, (clash.kind, clash.key)
    patch_files = sorted(p for p in index(sample_install, scan(sample_install)).layers[PATCH].files)
    assert patch_files == [
        "common/defines/zz_cold_steel_NGame.START_YEAR.txt",
        "common/technology/zz_cold_steel_tech_x.txt",
        "descriptor.mod",
        "events/!!_cold_steel_ev.1.txt",
        "gfx/models/thing.txt",
        "interface/zz_cold_steel_GFX_x.gfx",
        "localisation/replace/zz_cold_steel_l_english.yml",
    ]


def test_the_users_own_version_wins(sample_install: SampleInstall, tmp_path: Path) -> None:
    write(sample_install, CLASHES)
    _, idx, found = find(sample_install)
    choices = book(tmp_path)
    tech = conflict(found, "common/technology", "tech_x")
    loc = conflict(found, "localisation", "tech_x")
    whole = conflict(found, FILE, "gfx/models/thing.txt")
    choices.write_own(tech, "tech_x = { cost = 3 }", idx)
    choices.write_own(loc, 'tech_x:0 "Mine"', idx)
    choices.write_own(whole, "my whole file\n", idx)

    after = build(sample_install, tmp_path, choices)
    assert b"cost = 3" in text_of(after, conflict(after, "common/technology", "tech_x"))
    assert b'"Mine"' in text_of(after, conflict(after, "localisation", "tech_x"))
    assert text_of(after, conflict(after, FILE, "gfx/models/thing.txt")) == b"my whole file\n"


def test_a_changed_version_needs_another_look(
    sample_install: SampleInstall, tmp_path: Path
) -> None:
    write(sample_install, CLASHES)
    _, idx, found = find(sample_install)
    choices = book(tmp_path)
    tech = conflict(found, "common/technology", "tech_x")
    choices.choose(tech, tech.claims[claim_of(tech, BETA)], idx)

    # Spacing and comments don't count as a change.
    write(
        sample_install,
        {ALPHA: {"common/technology/b_alpha.txt": b"tech_x = {\n\tcost = 1 # hi\n}\n"}},
    )
    _, idx, found = find(sample_install)
    assert choices.state(conflict(found, "common/technology", "tech_x"), idx) == CHOSEN

    write(sample_install, {ALPHA: {"common/technology/b_alpha.txt": b"tech_x = { cost = 9 }\n"}})
    _, idx, found = find(sample_install)
    tech = conflict(found, "common/technology", "tech_x")
    assert choices.state(tech, idx) == STALE
    plan = plan_patch(found, choices.resolutions, idx)
    assert plan.files == {}
    assert "changed after you chose" in plan.left_out[0].why

    # Choosing again settles it.
    choices.choose(tech, tech.claims[claim_of(tech, BETA)], idx)
    assert choices.state(tech, idx) == CHOSEN


def test_choices_that_no_longer_apply_are_left_out(
    sample_install: SampleInstall, tmp_path: Path
) -> None:
    write(sample_install, CLASHES)
    _, idx, found = find(sample_install)
    choices = book(tmp_path)
    tech = conflict(found, "common/technology", "tech_x")
    choices.choose(tech, tech.claims[claim_of(tech, BETA)], idx)

    without_beta = Playset(id="p", name="Test", entries=(PlaysetEntry(ALPHA),))
    _, idx, found = find(sample_install, without_beta)
    plan = plan_patch(found, choices.resolutions, idx)
    assert plan.files == {}
    assert plan.left_out[0].why == "It isn't a conflict in this playset any more."


def test_choices_and_ignores_are_saved(sample_install: SampleInstall, tmp_path: Path) -> None:
    write(sample_install, CLASHES)
    _, idx, found = find(sample_install)
    choices = book(tmp_path)
    tech = conflict(found, "common/technology", "tech_x")
    event = conflict(found, "events", "ev.1")
    choices.choose(tech, tech.claims[0], idx)
    choices.ignore(Ignore(kind="events", key="ev.1"))

    again = book(tmp_path)
    assert again.state(tech, idx) == CHOSEN
    assert again.ignored_by(event) == Ignore(kind="events", key="ev.1")
    assert again.ignored_by(tech) is None

    again.ignore(Ignore(mod=ALPHA))
    assert again.ignored_by(tech) == Ignore(mod=ALPHA)
    again.stop_ignoring(Ignore(mod=ALPHA))
    again.ignore(Ignore(kind="common/technology"))
    assert again.ignored_by(tech) == Ignore(kind="common/technology")

    again.clear("common/technology", "tech_x")
    assert again.state(tech, idx) == NONE
    again.choose(tech, tech.claims[0], idx)
    again.clear_all()
    assert book(tmp_path).resolutions == ()

    copy_resolutions("p", "q", tmp_path / "resolutions")
    assert resolutions_file("q", tmp_path / "resolutions").exists()


def test_an_unreadable_file_is_kept(tmp_path: Path) -> None:
    path = resolutions_file("p", tmp_path / "r")
    path.parent.mkdir(parents=True)
    path.write_text("not json")
    opened = ResolutionBook.open(path)
    assert opened.resolutions == ()
    assert opened.problems
    assert [p.name for p in path.parent.iterdir() if "unreadable" in p.name]


def test_own_versions_are_checked(sample_install: SampleInstall, tmp_path: Path) -> None:
    write(sample_install, CLASHES)
    _, idx, found = find(sample_install)
    tech = conflict(found, "common/technology", "tech_x")
    loc = conflict(found, "localisation", "tech_x")
    define = conflict(found, "common/defines", "NGame.START_YEAR")

    assert check_own(tech, "tech_x = { cost = 3 }", idx) == []
    assert check_own(tech, "tech_x = {\n cost = 3 }\n}", idx)[0].startswith("Line 3: This")
    assert "doesn't define tech_x" in check_own(tech, "tech_y = { cost = 3 }", idx)[0]
    assert check_own(loc, 'tech_x:0 "Mine"', idx) == []
    assert check_own(loc, "tech_x Mine", idx)[0].startswith("Line 1: This line isn't")
    assert check_own(define, "START_YEAR = 2500", idx) == []
    assert "doesn't define" in check_own(define, "END_YEAR = 2500", idx)[0]


def test_file_names_sort_ahead_of_every_rival() -> None:
    assert winning_name(["b.txt", "zzz_x.txt"], "t.txt", first=False) == "zzzz_t.txt"
    assert winning_name(["00_a.txt", "!!!x.txt"], "t.txt", first=True) == "!!!!_t.txt"
    assert winning_name(["ZZZ_upper.txt"], "t.txt", first=False) == "zzzz_t.txt"
    # A real mod starts its name with "~" to sort last.
    assert winning_name(["~ariphaos_x.txt"], "t.txt", first=False) == "~~_t.txt"
    assert winning_name(["\x7fodd.txt"], "t.txt", first=False) is None


def test_writing_links_the_patch_into_the_mod_folder(
    sample_install: SampleInstall, tmp_path: Path
) -> None:
    write(sample_install, CLASHES)
    library, idx, found = find(sample_install)
    choices = book(tmp_path)
    tech = conflict(found, "common/technology", "tech_x")
    choices.choose(tech, tech.claims[0], idx)
    root = tmp_path / "patches"

    folder = write_patch(plan_patch(found, choices.resolutions, idx), PLAYSET, library.game, root)
    link = library.game.mod_dir / f"{PATCH_PREFIX}p"
    outer = library.game.mod_dir / f"{PATCH_PREFIX}p.mod"
    assert link.is_symlink() and link.resolve() == folder
    text = outer.read_text()
    assert 'name="Cold Steel patch: Test"' in text
    assert f'path="{link}"' in text
    assert 'supported_version="v' in text

    # A rebuild starts from nothing, so a cleared choice leaves no file behind.
    choices.clear_all()
    write_patch(plan_patch(found, choices.resolutions, idx), PLAYSET, library.game, root)
    assert sorted(p.name for p in folder.rglob("*") if p.is_file()) == ["descriptor.mod"]

    remove_patch("p", library.game, root)
    assert not link.is_symlink() and not outer.exists() and not folder.exists()


def test_something_in_the_way_is_never_replaced(
    sample_install: SampleInstall, tmp_path: Path
) -> None:
    library, idx, found = find(sample_install)
    (library.game.mod_dir / f"{PATCH_PREFIX}p").mkdir()
    plan = plan_patch(found, (), idx)
    with pytest.raises(PatchError, match="in the way"):
        write_patch(plan, PLAYSET, library.game, tmp_path / "patches")
    assert not (tmp_path / "patches").exists()


def test_the_patchs_workshop_copy_is_still_the_patch(
    sample_install: SampleInstall, tmp_path: Path
) -> None:
    """Uploaded and played from the Workshop, the patch must not count as
    another mod: a rebuild would then leave choices out, or lose to it."""
    write(sample_install, CLASHES)
    library, idx, found = find(sample_install)
    choices = book(tmp_path)
    tech = conflict(found, "common/technology", "tech_x")
    choices.choose(tech, tech.claims[0], idx)
    root = tmp_path / "patches"
    first = plan_patch(found, choices.resolutions, idx)
    folder = write_patch(first, PLAYSET, library.game, root)

    # The launcher uploads it and saves the id; the user plays the Workshop copy.
    uploaded = "workshop:3000000001"
    with (folder / "descriptor.mod").open("a") as descriptor:
        descriptor.write('remote_file_id="3000000001"\n')
    shutil.copytree(folder, sample_install.workshop_dir / "3000000001")
    withdraw(library.game, f"{PATCH_PREFIX}p")
    playset = Playset(id="p", name="Test", entries=(*PLAYSET.entries, PlaysetEntry(uploaded)))

    library = scan(sample_install)
    keys = patch_keys("p", root, library.game)
    assert keys == {PATCH, uploaded}
    idx = index(sample_install, library)
    found = ConflictFinder(idx, playset, library, leave_out=keys)(JobContext())
    again = plan_patch(found, choices.resolutions, idx)
    assert again.files == first.files and again.left_out == ()
    # The local copy still goes last, beside the Workshop one; the user picks which plays.
    assert [e.key for e in with_patch_last(playset).entries][-2:] == [uploaded, PATCH]

    # A rebuild keeps the id, so the launcher still updates the Workshop copy.
    write_patch(again, playset, library.game, root)
    assert 'remote_file_id="3000000001"' in (folder / "descriptor.mod").read_text()
    assert (
        'remote_file_id="3000000001"' in (library.game.mod_dir / f"{PATCH_PREFIX}p.mod").read_text()
    )


def test_the_patch_goes_last_and_only_once() -> None:
    first = with_patch_last(PLAYSET)
    moved = Playset(id="p", name="Test", entries=(first.entries[-1], *PLAYSET.entries))
    again = with_patch_last(moved)
    assert [e.key for e in again.entries] == [ALPHA, BETA, LOCAL, PATCH]
