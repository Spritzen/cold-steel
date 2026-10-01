# Phase 0: Foundation

**Status:** In progress
**Result:** An empty Cold Steel window opens, and one command runs every check.

## Done when

- [x] Git repo created and pushed to GitHub
      ([Spritzen/cold-steel](https://github.com/Spritzen/cold-steel), branch `main`)
- [ ] `.gitignore` added, and `.directory` (a KDE folder-view file,
      committed by accident) removed from the repo
- [ ] A licence file is committed (see open questions)
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
| Licence? (Moved here from Phase 7. The repo is already public, and without a licence nobody else may legally use the code) | MIT, the same as Irony, which also lets us use Irony's rules data |
| Add a docs link checker like STG's `check_docs.py`? | Yes, but a small one: just "does every link resolve", run by `make check` |
