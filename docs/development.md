# Development

**Everything runs through `make`.** Run `make check` before every commit.

| Command | Does |
|---|---|
| `make run` | Opens the app |
| `make check` | Everything below except `bench`. Must pass before merging |
| `make lint` | ruff (lint and format check), then mypy |
| `make format` | Fixes formatting and the lint issues ruff can fix itself |
| `make test` | Runs the tests |
| `make bench` | Runs only the timing benchmarks |
| `make docs` | Checks that every link in the docs points at a real file and heading |

## Setup

Open the repo in the dev container ([.devcontainer/](../.devcontainer/)). It
installs every package from pacman ([decision 2](decisions.md)). There is no
venv and nothing to `pip install`.

The code isn't installed as a package. `make` sets `PYTHONPATH=src`, and
pytest does the same through `pyproject.toml`. To run Python by hand:

```sh
PYTHONPATH=src python -m cold_steel
```

## Tests

- GUI tests use [pytest-qt](https://pytest-qt.readthedocs.io/). They run
  headless by default. To watch them on screen:
  `COLD_STEEL_TEST_QPA=wayland make test`.
- Timing tests use pytest-benchmark's `benchmark` fixture. `make test` skips
  them; `make bench` runs only them.
- Code in `core/`, `store/` and `paradox/` should be tested without Qt.
- `tests/fixtures/` holds a small fake install (two Steam libraries, three
  Workshop mods, a local mod, a launcher database). `conftest.py` copies it to
  a temporary folder for each test, and builds the binary parts there.
- One test, marked `real_install`, scans your real install with the cache in a
  temporary folder, then checks that nothing in your Paradox folder changed.
  It's skipped on machines without Stellaris.

## Type checking

mypy runs in strict mode on `src/`, `tests/` and `tools/`
([decision 3](decisions.md)). `tests/` is on mypy's path so tests can import
helpers from `conftest.py`. PySide6 from pacman ships type stubs but no
`py.typed` marker, so `pyproject.toml` tells mypy to use them anyway.

## Branches

Work on a branch, then open a pull request into `main`. Name branches after
the phase or the change, for example `phase-0/foundation`.
