"""Phase 6: merging a playset into one mod, and linking it into the mod folder.

The main check: build a playset with clashes and a patch mod, then the Phase 4
winner rules must pick the same winner for every clash in the built mod.
"""

from dataclasses import replace
from pathlib import Path

from cold_steel.core.build import (
    BUILD_PREFIX,
    Builder,
    BuildRecord,
    build_key,
    check_build,
    load_record,
    plan_build,
    remove_build,
)
from cold_steel.core.conflicts import ConflictFinder
from cold_steel.core.index import GAME
from cold_steel.core.jobs import JobContext
from cold_steel.core.library import Library
from cold_steel.core.merge_rules import load_rules
from cold_steel.core.patch import plan_patch, with_patch_last, write_patch
from cold_steel.core.playsets import as_played
from cold_steel.core.resolve import ResolutionBook
from cold_steel.store.playsets import Playset
from cold_steel.store.resolutions import resolutions_file
from conftest import SampleInstall
from test_conflicts import ALPHA, BETA, LOCAL, folders, index, scan, write
from test_resolve import CLASHES, PATCH, PLAYSET, find
from test_snapshots import pin, store_of


def with_patch(install: SampleInstall, tmp_path: Path) -> tuple[Library, Playset]:
    """The clashing playset, with the losing version of every clash chosen."""
    write(install, CLASHES)
    library, idx, found = find(install)
    choices = ResolutionBook.open(resolutions_file("p", tmp_path / "resolutions"))
    for clash in found.conflicts:
        if len(clash.mods) > 1:
            loser = next(c for c in clash.claims if c.layer not in (clash.winning.layer, GAME))
            choices.choose(clash, loser, idx)
    plan = plan_patch(found, choices.resolutions, idx)
    write_patch(plan, PLAYSET, library.game, tmp_path / "patches")
    return scan(install), with_patch_last(PLAYSET)


def build(
    install: SampleInstall, tmp_path: Path, library: Library, playset: Playset
) -> BuildRecord:
    cache = install.cache_file.parent / "index"
    record, _ = Builder(library, playset, cache, tmp_path / "builds")(JobContext())
    return record


def test_the_build_plays_like_the_playset(sample_install: SampleInstall, tmp_path: Path) -> None:
    library, playset = with_patch(sample_install, tmp_path)
    record = build(sample_install, tmp_path, library, playset)
    assert record.mismatches == []
    assert record.order == (ALPHA, BETA, LOCAL, PATCH)

    folder = tmp_path / "builds/p"
    # Beta's whole file replaced Alpha's, then the patch, loaded last, put back
    # Alpha's, as the user chose.
    assert (folder / "gfx/models/thing.txt").read_bytes() == b"alpha's whole file\n"
    thing = record.files["gfx/models/thing.txt"]
    assert (thing.layer, thing.replaced) == (PATCH, (ALPHA, BETA))
    assert len([f for f in record.files.values() if f.layer == PATCH]) >= 6
    # Every file says where it came from, and the build matches the record.
    on_disk = {p.relative_to(folder).as_posix() for p in folder.rglob("*") if p.is_file()} - {
        "descriptor.mod"
    }
    assert on_disk == set(record.files)
    assert record.names[ALPHA] == "Alpha Interface"

    # Loose files at the top of a mod aren't copied.
    left_out = {path for _, path in record.left_out}
    assert {"descriptor.mod", "thumbnail.png"} <= left_out
    assert not (folder / "thumbnail.png").exists()

    # The record is saved, so the report can be shown later.
    saved = load_record(tmp_path / "builds", "p")
    assert saved is not None and saved.files == record.files


def test_the_build_is_linked_into_the_mod_folder(
    sample_install: SampleInstall, tmp_path: Path
) -> None:
    library, playset = with_patch(sample_install, tmp_path)
    build(sample_install, tmp_path, library, playset)
    link = library.game.mod_dir / f"{BUILD_PREFIX}p"
    outer = library.game.mod_dir / f"{BUILD_PREFIX}p.mod"
    assert link.is_symlink() and link.resolve() == tmp_path / "builds/p"
    text = outer.read_text()
    assert 'name="Cold Steel build: Test"' in text
    # The path the game reads is the full path on this machine, which the
    # dev container shares with the host.
    assert f'path="{link}"' in text and link.is_absolute()

    # The scan finds it as a mod of its own.
    library = scan(sample_install)
    built = next(m for m in library.mods if m.key == build_key("p"))
    assert built.name == "Cold Steel build: Test"

    remove_build("p", tmp_path / "builds", library.game)
    assert not link.is_symlink() and not outer.exists()
    assert not (tmp_path / "builds/p").exists()


def test_a_rebuild_only_copies_what_changed(sample_install: SampleInstall, tmp_path: Path) -> None:
    library, playset = with_patch(sample_install, tmp_path)
    first = build(sample_install, tmp_path, library, playset)
    assert first.copied == len(first.files)

    again = build(sample_install, tmp_path, scan(sample_install), playset)
    assert again.copied == 0

    write(sample_install, {BETA: {"events/beta.txt": b"# beta, updated\n"}})
    changed = build(sample_install, tmp_path, scan(sample_install), playset)
    assert changed.copied == 1
    assert (tmp_path / "builds/p/events/beta.txt").read_bytes() == b"# beta, updated\n"

    # A file changed by hand in the build is put back.
    hand = tmp_path / "builds/p/events/b_alpha.txt"
    hand.write_bytes(b"edited\n")
    fixed = build(sample_install, tmp_path, scan(sample_install), playset)
    assert fixed.copied == 1 and hand.read_bytes() != b"edited\n"

    # A file no mod has any more is removed.
    (folders(sample_install)[ALPHA] / "events/b_alpha.txt").unlink()
    gone = build(sample_install, tmp_path, scan(sample_install), playset)
    assert "events/b_alpha.txt" not in gone.files and not hand.exists()


def test_paths_that_only_differ_in_case_are_one_file(
    sample_install: SampleInstall, tmp_path: Path
) -> None:
    write(
        sample_install,
        {
            ALPHA: {"Interface/Same.gui": b"alpha\n", "Interface/a.gui": b"a\n"},
            BETA: {"interface/same.gui": b"beta\n"},
        },
    )
    library = scan(sample_install)
    plan = plan_build([ALPHA, BETA], index(sample_install, library))
    # The folder keeps the first spelling; the file is Beta's, which loaded last.
    assert plan.files["Interface/same.gui"].layer == BETA
    assert plan.files["Interface/same.gui"].replaced == (ALPHA,)
    assert "Interface/Same.gui" not in plan.files and "Interface/a.gui" in plan.files


def test_the_check_finds_a_build_that_plays_differently(
    sample_install: SampleInstall, tmp_path: Path
) -> None:
    library, playset = with_patch(sample_install, tmp_path)
    idx = index(sample_install, library)
    found = ConflictFinder(idx, playset, library)(JobContext())
    plan = plan_build([k for k in found.order if k != GAME], idx)
    assert check_build(found, plan, idx, load_rules()) == []

    # Without the patch's technology file, Alpha's tech_x would win again.
    (path,) = (p for p, f in plan.files.items() if f.layer == PATCH and "technology" in p)
    wrong = replace(plan, files={p: f for p, f in plan.files.items() if p != path})
    (mismatch,) = check_build(found, wrong, idx, load_rules())
    assert (mismatch.kind, mismatch.key) == ("common/technology", "tech_x")
    assert (mismatch.expected, mismatch.got) == (PATCH, ALPHA)


def test_a_pinned_playset_builds_from_its_copies(
    sample_install: SampleInstall, tmp_path: Path
) -> None:
    library, playset = with_patch(sample_install, tmp_path)
    store = store_of(tmp_path)
    playset, taken = pin(store, library, playset, ALPHA, BETA)
    library = scan(sample_install)
    record = build(sample_install, tmp_path, library, as_played(playset))
    assert record.mismatches == []

    # Files from a pinned copy are hard links to it: no extra space.
    path, file = next((p, f) for p, f in record.files.items() if f.layer.startswith(ALPHA))
    built = tmp_path / "builds/p" / path
    copy = store.folder(taken[ALPHA].id) / file.source
    assert built.samefile(copy)
