"""Sort: dependencies, then the load-first/last rules, then the user's order."""

from cold_steel.core.load_order import load_rules, sort_playset
from cold_steel.core.mods import Mod
from cold_steel.store.playsets import Playset, PlaysetEntry


def sort(mods: list[Mod]) -> list[str]:
    playset = Playset(id="x", name="x", entries=tuple(PlaysetEntry(m.key) for m in mods))
    result = sort_playset(playset, {m.key: m for m in mods})
    names = {m.key: m.name for m in mods}
    return [names[e.key] for e in result.entries]


def mod(name: str, *deps: str) -> Mod:
    return Mod(key=f"local:{name}", source="local", name=name, dependencies=deps)


def test_keeps_the_users_order_when_no_rule_applies() -> None:
    assert sort([mod("Zeta"), mod("Alpha"), mod("Mid")]) == ["Zeta", "Alpha", "Mid"]


def test_patches_go_last_and_bang_names_first() -> None:
    mods = [
        mod("Ships Compatibility Patch"),
        mod("Ships"),
        mod("~~Late Fixes"),
        mod("!!Early"),
        mod("Planets"),
    ]
    assert sort(mods) == [
        "!!Early",
        "Ships",
        "Planets",
        "Ships Compatibility Patch",
        "~~Late Fixes",
    ]


def test_a_mod_loads_after_its_dependencies() -> None:
    # Declared names are matched ignoring case.
    mods = [mod("UI Submod", "ui overhaul"), mod("Other"), mod("UI Overhaul")]
    assert sort(mods) == ["Other", "UI Overhaul", "UI Submod"]


def test_dependencies_beat_the_rules() -> None:
    mods = [mod("!First", "Base"), mod("Base")]
    assert sort(mods) == ["Base", "!First"]


def test_a_dependency_loop_breaks_at_the_users_first_mod() -> None:
    # Mods that can be placed go first; then the loop is broken at A.
    mods = [mod("A", "B"), mod("B", "A"), mod("C")]
    assert sort(mods) == ["C", "A", "B"]


def test_unknown_dependencies_are_ignored() -> None:
    assert sort([mod("A", "Not Installed"), mod("B")]) == ["A", "B"]


def test_the_rules_file_loads() -> None:
    rules = load_rules()
    assert rules.first and rules.last
