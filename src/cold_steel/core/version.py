"""Is a mod made for an older game version?

A mod is **outdated** when the major.minor version it supports is older than the
game's. The patch number is ignored, as the game itself does: `launcher-settings.json`
gives `modsCompatibilityVersion` as "4.5" for game v4.5.1. A `*` matches anything.

    is_outdated("v4.5.*", "v4.5.1")  -> False
    is_outdated("v4.4.*", "v4.5.1")  -> True
    is_outdated("v4.*.*", "v4.5.1")  -> False
"""


def is_outdated(supported: str, game_version: str) -> bool:
    """False when either version can't be read: we don't flag what we can't tell."""
    mod = _major_minor(supported)
    game = _major_minor(game_version)
    if mod is None or game is None:
        return False
    for m, g in zip(mod, game, strict=True):
        if m is None or g is None:  # a wildcard: everything after it matches
            return False
        if m != g:
            return m < g
    return False


def _major_minor(version: str) -> tuple[int | None, int | None] | None:
    parts = version.strip().removeprefix("v").removeprefix("V").split(".")
    numbers: list[int | None] = []
    for part in [*parts, "*"][:2]:  # "3" alone means "3.*"
        if "*" in part:
            numbers.append(None)
        elif part.isdigit():
            numbers.append(int(part))
        else:
            return None
    return numbers[0], numbers[1]
