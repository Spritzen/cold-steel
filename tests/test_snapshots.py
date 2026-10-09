"""Pinned copies of Workshop mods.

The checks follow Phase 6's "Done when" list: pinning copies each Workshop
mod exactly, an update shows what changed before you accept it, a pinned
playset survives unsubscribing, and copies share the files they have in common.
"""

import os
import shutil
from pathlib import Path

from cold_steel.core.conflicts import ConflictFinder
from cold_steel.core.jobs import JobContext
from cold_steel.core.library import Library
from cold_steel.core.mods import base_key, pinned_key
from cold_steel.core.play import plan_play
from cold_steel.core.playsets import as_played, missing_mods, set_pins, unpin
from cold_steel.core.resolve import CHOSEN, ResolutionBook
from cold_steel.core.snapshots import (
    Drift,
    PinChecker,
    Pinner,
    Snapshot,
    SnapshotStore,
    hash_file,
    pin_name,
)
from cold_steel.store.playsets import Pin, Playset, PlaysetEntry
from cold_steel.store.resolutions import resolutions_file
from conftest import SampleInstall, snapshot
from test_conflicts import ALPHA, BETA, LOCAL, conflict, index, scan, write

GAMMA = "workshop:2000000003"  # shipped as one zip
PLAYSET = Playset(
    id="p",
    name="Test",
    entries=(PlaysetEntry(ALPHA), PlaysetEntry(BETA), PlaysetEntry(GAMMA), PlaysetEntry(LOCAL)),
)


def store_of(tmp_path: Path) -> SnapshotStore:
    return SnapshotStore(tmp_path / "snapshots")


def pin(
    store: SnapshotStore, library: Library, playset: Playset, *keys: str
) -> tuple[Playset, dict[str, Snapshot]]:
    keys = keys or (ALPHA, BETA, GAMMA)
    previous = {p.key: p.snapshot for p in playset.pins}
    taken = Pinner(store, library, keys, previous)(JobContext())
    return set_pins(playset, [Pin(k, s.id) for k, s in taken.items()]), taken


def contents(root: Path) -> dict[str, tuple[int, str]]:
    """Every file's size and hash, ignoring timestamps."""
    return {path: (size, digest) for path, (size, _, digest) in snapshot(root).items()}


def check(store: SnapshotStore, library: Library, playset: Playset) -> dict[str, Drift]:
    found = PinChecker(store, library, tuple(p.snapshot for p in playset.pins))(JobContext())
    return {snap.key: drift for snap, drift in found.values()}


def test_pinning_copies_each_workshop_mod_exactly(
    sample_install: SampleInstall, tmp_path: Path
) -> None:
    library = scan(sample_install)
    store = store_of(tmp_path)
    playset, taken = pin(store, library, PLAYSET)

    for key, snap in taken.items():
        workshop = sample_install.workshop_dir / key.partition(":")[2]
        assert contents(store.folder(snap.id)) == contents(workshop)
    # Local mods are the user's own, so they aren't pinned.
    assert {p.key for p in playset.pins} == {ALPHA, BETA, GAMMA}

    # The scan finds each copy, and the playset plays the copies.
    library = scan(sample_install)
    copies = {m.key: m for m in library.pinned}
    assert set(copies) == {pinned_key(k, s.id) for k, s in taken.items()}
    assert {m.key for m in library.mods}.isdisjoint(copies)  # never listed as mods
    assert copies[pinned_key(ALPHA, taken[ALPHA].id)].name == "Alpha Interface"
    zipped = copies[pinned_key(GAMMA, taken[GAMMA].id)]
    assert zipped.archive == str(store.folder(taken[GAMMA].id) / "gamma.zip")

    load = plan_play(as_played(playset), library).load.enabled_mods
    assert load == (
        f"mod/{pin_name(ALPHA, taken[ALPHA].id)}.mod",
        f"mod/{pin_name(BETA, taken[BETA].id)}.mod",
        f"mod/{pin_name(GAMMA, taken[GAMMA].id)}.mod",
        "mod/my_local.mod",
    )
    # Unpinning plays Steam's folders again.
    assert plan_play(as_played(unpin(playset)), library).load.enabled_mods[0] == (
        "mod/ugc_2000000001.mod"
    )


def test_an_update_shows_what_changed_before_it_is_accepted(
    sample_install: SampleInstall, tmp_path: Path
) -> None:
    library = scan(sample_install)
    store = store_of(tmp_path)
    playset, taken = pin(store, library, PLAYSET)
    assert not any(d.updated for d in check(store, library, playset).values())

    alpha = sample_install.workshop_dir / "2000000001"
    (alpha / "common/alpha.txt").write_text("changed = yes\n")
    (alpha / "common/new.txt").write_text("new = yes\n")
    (alpha / "thumbnail.png").unlink()
    # Steam re-touched this one without changing it: not an update.
    os.utime(alpha / "descriptor.mod", ns=(1, 1))

    library = scan(sample_install)
    drift = check(store, library, playset)[ALPHA]
    assert drift.changed == ("common/alpha.txt",)
    assert drift.added == ("common/new.txt",)
    assert drift.removed == ("thumbnail.png",)
    # Until accepted, the playset still plays the old copy.
    old_copy = store.folder(taken[ALPHA].id) / "common/alpha.txt"
    assert old_copy.read_text() != "changed = yes\n"

    accepted, again = pin(store, library, playset, ALPHA)
    assert again[ALPHA].id != taken[ALPHA].id
    assert {p.key: p.snapshot for p in accepted.pins}[BETA] == taken[BETA].id
    library = scan(sample_install)
    assert not check(store, library, accepted)[ALPHA].updated


def test_a_pinned_playset_survives_unsubscribing(
    sample_install: SampleInstall, tmp_path: Path
) -> None:
    library = scan(sample_install)
    store = store_of(tmp_path)
    playset, taken = pin(store, library, PLAYSET)
    shutil.rmtree(sample_install.workshop_dir / "2000000001")  # unsubscribed

    library = scan(sample_install)
    assert ALPHA not in {m.key for m in library.mods}
    plan = plan_play(as_played(playset), library)
    assert plan.skipped == ()
    assert plan.load.enabled_mods[0] == f"mod/{pin_name(ALPHA, taken[ALPHA].id)}.mod"
    assert check(store, library, playset)[ALPHA].gone
    # The mod list shows the copy in its place, not a missing mod.
    (stand_in,) = (m for m in missing_mods([playset], library) if m.key == ALPHA)
    assert stand_in.installed and stand_in.name == "Alpha Interface"


def test_copies_share_the_files_they_have_in_common(
    sample_install: SampleInstall, tmp_path: Path
) -> None:
    write(
        sample_install, {ALPHA: {f"common/big_{n}.txt": str(n).encode() * 5000 for n in range(5)}}
    )
    library = scan(sample_install)
    store = store_of(tmp_path)
    first, taken = pin(store, library, PLAYSET)
    # Pinning the same mods in another playset reuses the same copies.
    _, again = pin(store, library, Playset(id="q", name="Other", entries=PLAYSET.entries))
    assert {k: s.id for k, s in again.items()} == {k: s.id for k, s in taken.items()}

    objects = sorted((store.root / "objects").glob("*/*"))
    write(sample_install, {ALPHA: {"common/big_0.txt": b"y" * 5000}})
    library = scan(sample_install)
    _, updated = pin(store, library, first, ALPHA)
    # One file changed, so one object more. The rest are the same files on disk.
    assert len(sorted((store.root / "objects").glob("*/*"))) == len(objects) + 1
    old, new = store.folder(taken[ALPHA].id), store.folder(updated[ALPHA].id)
    assert (old / "common/big_1.txt").stat().st_ino == (new / "common/big_1.txt").stat().st_ino
    assert (old / "common/big_0.txt").stat().st_ino != (new / "common/big_0.txt").stat().st_ino

    # Keeping only the new copy deletes the old one and the object only it used.
    freed = store.collect({updated[ALPHA].id, taken[BETA].id, taken[GAMMA].id}, library.game)
    assert freed == 5000
    assert not old.exists() and new.exists()
    assert not (library.game.mod_dir / f"{pin_name(ALPHA, taken[ALPHA].id)}.mod").exists()
    for path, file in updated[ALPHA].files.items():
        assert hash_file(new / path) == file.hash


def test_choices_carry_over_to_the_pinned_copy(
    sample_install: SampleInstall, tmp_path: Path
) -> None:
    write(
        sample_install,
        {
            ALPHA: {"common/technology/b.txt": b"tech_x = { cost = 1 }\n"},
            BETA: {"common/technology/a.txt": b"tech_x = { cost = 2 }\n"},
        },
    )
    library = scan(sample_install)
    idx = index(sample_install, library)
    found = ConflictFinder(idx, PLAYSET, library)(JobContext())
    tech = conflict(found, "common/technology", "tech_x")
    choices = ResolutionBook.open(resolutions_file("p", tmp_path / "resolutions"))
    choices.choose(tech, next(c for c in tech.claims if c.layer == BETA), idx)

    playset, taken = pin(store_of(tmp_path), library, PLAYSET)
    library = scan(sample_install)
    idx = index(sample_install, library, idx)
    found = ConflictFinder(idx, as_played(playset), library)(JobContext())
    tech = conflict(found, "common/technology", "tech_x")
    assert {c.layer for c in tech.claims} >= {pinned_key(BETA, taken[BETA].id)}
    assert {base_key(c.layer) for c in tech.claims} >= {ALPHA, BETA}
    assert choices.state(tech, idx) == CHOSEN
