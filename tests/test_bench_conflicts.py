"""Timing: conflicts on your real install. Phase 4's speed targets.

- The first full index of the game and every installed mod: under 30 s.
- Finding conflicts again after one mod changed: under 2 s. Measured on your
  largest playset, with its biggest mod read again from scratch.

Run with `make bench`. Skipped on machines without Stellaris. The cache goes
to a temporary folder, and nothing in the install is written.
"""

import os
import shutil
from pathlib import Path

import pytest
from pytest_benchmark.fixture import BenchmarkFixture

from cold_steel.core.conflicts import ConflictFinder, Found
from cold_steel.core.index import Index, Indexer
from cold_steel.core.jobs import JobContext
from cold_steel.core.library import Library, Scanner
from cold_steel.paradox.game import DEFAULT_STEAM_DIRS, GameNotFound, find_game
from cold_steel.store.playsets import Playset, PlaysetEntry, load_playsets

pytestmark = pytest.mark.real_install


@pytest.fixture(scope="module")
def library(tmp_path_factory: pytest.TempPathFactory) -> Library:
    steam_dirs = (
        tuple([Path(os.environ["STEAM_DIR"])] if "STEAM_DIR" in os.environ else [])
        + DEFAULT_STEAM_DIRS
    )
    try:
        find_game(steam_dirs)
    except GameNotFound:
        pytest.skip("Stellaris isn't installed here")
    cache = tmp_path_factory.mktemp("scan") / "mods.msgpack"
    return Scanner(steam_dirs, cache)(JobContext())


def every_mod(library: Library) -> Playset:
    """A playset of every installed mod: the most there can be to compare."""
    entries = tuple(PlaysetEntry(m.key) for m in library.mods if m.installed)
    return Playset(id="bench", name="Every mod", entries=entries)


def largest_playset(library: Library) -> Playset:
    """Your largest playset. Every mod at once overstates it: no one plays a merged
    mod like Star Trek Galaxies alongside the mods it was merged from.
    """
    saved = load_playsets()
    playsets = saved.playsets if saved else []
    return max(playsets, key=lambda p: len(p.entries), default=every_mod(library))


def test_first_full_index(benchmark: BenchmarkFixture, library: Library, tmp_path: Path) -> None:
    cache = tmp_path / "index"

    def empty_cache() -> None:
        shutil.rmtree(cache, ignore_errors=True)

    def first_time() -> Found:
        index = Indexer(library, cache)(JobContext())
        return ConflictFinder(index, every_mod(library), library)(JobContext())

    found: Found = benchmark.pedantic(first_time, setup=empty_cache, rounds=3)  # type: ignore[no-untyped-call]
    assert found.conflicts
    assert benchmark.stats is not None
    assert benchmark.stats.stats.max < 30


def test_again_after_one_mod_changed(
    benchmark: BenchmarkFixture, library: Library, tmp_path: Path
) -> None:
    cache = tmp_path / "index"
    full = Indexer(library, cache)(JobContext())
    playset = largest_playset(library)
    # The playset's biggest mod changed: it's read again from scratch, file by file.
    biggest = max(
        (e.key for e in playset.entries if e.key in full.layers),
        key=lambda k: sum(len(f.definitions) for f in full.layers[k].files.values()),
    )
    previous = Index(
        full.language, {k: v for k, v in full.layers.items() if k != biggest}, full.sources
    )
    cache_file = next(cache.glob(biggest.replace(":", "-") + ".msgpack"))

    def forget_it() -> None:
        cache_file.unlink(missing_ok=True)

    def again() -> Found:
        index = Indexer(library, cache, previous)(JobContext())
        return ConflictFinder(index, playset, library)(JobContext())

    benchmark.pedantic(again, setup=forget_it, rounds=5)  # type: ignore[no-untyped-call]
    assert benchmark.stats is not None
    assert benchmark.stats.stats.max < 2
