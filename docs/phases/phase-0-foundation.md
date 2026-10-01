# Phase 0: Foundation

**Status:** Done
**Result:** An empty Cold Steel window opens, and one command runs every check.

## Done when

- [x] Git repo created and pushed to GitHub
      ([Spritzen/cold-steel](https://github.com/Spritzen/cold-steel), branch `main`)
- [x] `.gitignore` added, and `.directory` (a KDE folder-view file,
      committed by accident) removed from the repo
- [x] A licence file is committed (MIT, [decision 12](../decisions.md))
- [x] Running the app opens an empty Cold Steel window on the desktop
- [x] `make check` runs ruff, mypy and pytest, and passes
- [x] One GUI test (pytest-qt) opens and closes the window
- [x] [docs/architecture/README.md](../architecture/README.md) exists and shows the code layout below
- [x] A [dev guide](../development.md) says how to run, test and lint

## What it contains

**Project skeleton**
- `pyproject.toml`, holding project info and tool settings (ruff, mypy,
  pytest) only. Packages still come from pacman ([decision 2](../decisions.md)).
- `Makefile` with `run`, `check`, `lint`, `format`, `test`, `bench`.
- `src/cold_steel/` package and `tests/` folder.

**Code layout.** The rule: the core never imports Qt, so it can be tested and
timed without a window.

| Folder | Job |
|---|---|
| `core/` | Plain Python: finding mods, parsing, conflicts, building. No Qt |
| `store/` | Saving and loading our own data, plus the parse cache |
| `paradox/` | Reading and writing Paradox files (launcher DB, `.mod`, `dlc_load.json`) |
| `ui/` | Qt windows and widgets. Calls into `core`, never the other way |

**Background work.** A small helper that runs core jobs off the main
thread and reports progress to the window. It's built now so no later feature
blocks the window ([decision 9](../decisions.md)).

## Borrowed from

- STG: the `make` habit (`make check`, `make docs`).

## Open questions

None. The three questions here became decisions
[11–13](../decisions.md).
