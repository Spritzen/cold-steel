"""Timing: the second start with 60 mods must list them in well under a second.

Run with `make bench`. `make test` skips these.
"""

import pytest
from pytest_benchmark.fixture import BenchmarkFixture

from cold_steel.core.jobs import JobContext
from cold_steel.core.library import Library, Scanner
from conftest import SampleInstall

MODS = 60
FILES_PER_MOD = 400  # about what a mid-sized Workshop mod has


@pytest.fixture
def big_install(sample_install: SampleInstall) -> SampleInstall:
    for n in range(MODS):
        mod = sample_install.workshop_dir / str(3_000_000_000 + n)
        (mod / "common/things").mkdir(parents=True)
        (mod / "descriptor.mod").write_text(
            f'name="Bench Mod {n}"\ntags={{ "Gameplay" }}\nsupported_version="v4.5.*"\n'
        )
        for f in range(FILES_PER_MOD):
            (mod / f"common/things/{f}.txt").write_text(f"thing_{f} = {{ value = {f} }}\n")
    return sample_install


def test_second_scan_uses_the_cache(
    benchmark: BenchmarkFixture, big_install: SampleInstall
) -> None:
    scanner = Scanner(*big_install.scanner_args())
    first = scanner(JobContext())  # fills the cache
    assert len(first.mods) >= MODS

    result: Library = benchmark(scanner, JobContext())

    assert result == first
    assert benchmark.stats is not None
    mean: float = benchmark.stats.stats.mean
    assert mean < 0.5, "the cached scan should leave room for the window to open"
