# Phase 3: Diagnose

**Status:** Not started
**Result:** Before playing, you can see which mods have broken files. After
playing, you can see which mod caused each error in the game's log.

These are two of the features taken from STG
([from-stg.md](../reference/from-stg.md)). Neither needs the full conflict
system, which is why they come before it.

## Done when

- [ ] Each mod in the list shows a health badge: OK, warnings or errors
- [ ] Clicking the badge lists each problem with the file and line, in plain
      words ("this localisation file is missing its BOM, so the game will
      ignore it")
- [ ] Health checks run in the background and use the Phase 1 cache, so
      they're only redone for changed mods
- [ ] After a game run, an "Errors" view reads `error.log` and groups the
      errors by the mod they came from
- [ ] Errors that can't be traced to a mod are grouped as "game / unknown",
      not hidden
- [ ] Each check has a test using a small broken sample mod

## What it contains

**Health checks.** These are file-level problems that make the game quietly
ignore part of a mod:

| Check | What goes wrong in-game |
|---|---|
| Localisation `.yml` missing its UTF-8 BOM | The whole file is ignored, so text shows as raw keys |
| Localisation line missing its `:0` version | That line is ignored |
| Unbalanced `{ }` braces in script | The rest of the file after the break is ignored |
| `supported_version` older than the game | The launcher marks the mod outdated |
| Descriptor problems (missing name, bad path) | The mod doesn't appear or doesn't load |

**Error log reader.** `error.log` lines name a file path. We match that path
against the playset's mods: the last mod in load order that has that file is
the one the game was reading. Show the source line where possible.

## Borrowed from

- STG `validate.py`: the checks in the table above. Only these generic checks.
  Most of its 7,800 lines are specific to Star Trek Galaxies and don't apply.
- STG `logs.py`: knowing which log files exist and which matter.

## Open questions

| Question | Recommendation |
|---|---|
| Copy STG's code, or rewrite? | Rewrite, using STG as the specification. Its checks are tied to its own build layout and `make` targets. Copy the test cases |
| Watch `error.log` live while the game runs? | Not now. Read it on request and after the game exits. Live watching can come later if it's wanted |
