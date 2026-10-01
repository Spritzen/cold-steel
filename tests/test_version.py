import pytest

from cold_steel.core.version import is_outdated


@pytest.mark.parametrize(
    ("supported", "outdated"),
    [
        ("v4.5.*", False),
        ("v4.5.1", False),
        ("v4.5.0", False),  # the patch number doesn't matter
        ("4.5.*", False),
        ("v4.*.*", False),
        ("v4.**.*", False),  # a typo seen on the Workshop; still a wildcard
        ("v4.6.*", False),  # newer than the game isn't outdated
        ("v4.4.*", True),
        ("v4.4.6", True),
        ("3.11.2", True),
        ("3.*", True),
        ("2.1.*", True),
        ("", False),  # unknown: don't flag it
        ("any", False),
    ],
)
def test_outdated_compares_major_and_minor(supported: str, outdated: bool) -> None:
    assert is_outdated(supported, "v4.5.1") is outdated
