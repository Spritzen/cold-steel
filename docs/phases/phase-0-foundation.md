# Phase 0: Foundation

**Status:** Not started
**Result:** An empty Cold Steel window opens, and one command runs every check.

## Done when

- [ ] `git init` done, with a `.gitignore`; pushed to GitHub
- [ ] Running the app opens an empty Cold Steel window on the desktop
- [ ] `make check` runs ruff, mypy and pytest, and passes
- [ ] One GUI test (pytest-qt) opens and closes the window
- [ ] `docs/architecture/README.md` exists and shows the code layout below
- [ ] A dev guide says how to run, test and lint

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

| Question | Recommendation |
|---|---|
| Where does Cold Steel keep its own data? | XDG paths: `~/.config/cold-steel/` for settings, `~/.local/share/cold-steel/` for playsets and the patch-mod work, `~/.cache/cold-steel/` for the parse cache (safe to delete) |
| GitHub repo name and visibility? | `cold-steel`, private until Phase 7 |
| Add a docs link checker like STG's `check_docs.py`? | Yes, but a small one: just "does every link resolve", run by `make check` |
