# Cold Steel

A native Linux mod manager for **Stellaris**, written in Python + Qt 6
(PySide6). Repo: [Spritzen/cold-steel](https://github.com/Spritzen/cold-steel).

It replaces and improves on
[IronyModManager](https://github.com/bcssov/IronyModManager), and borrows four
features from our Stellaris mod project at `$STELLARIS_FRAMEWORK_DIR`.

## Start here

| I want to… | Go to |
|---|---|
| know what we're building and what we're not | [docs/scope.md](docs/scope.md) |
| know what to work on now | [docs/phases/README.md](docs/phases/README.md) |
| check whether something is already decided | [docs/decisions.md](docs/decisions.md) |
| run, test or lint the code | [docs/development.md](docs/development.md) |
| see how the code is laid out | [docs/architecture/](docs/architecture/README.md) |
| find where Stellaris keeps its files | [docs/reference/stellaris-files.md](docs/reference/stellaris-files.md) |
| see everything else | [docs/README.md](docs/README.md) |

## Rules that always apply

1. **Back up before writing Paradox files.** `launcher-v2.sqlite` holds the
   user's real playsets. Read-only until Phase 2, and every write after that
   is preceded by a backup.
2. **Steam and game folders are read-only.** Mounted that way in the
   container; the app must never need to write there.
3. **Decisions go in [docs/decisions.md](docs/decisions.md)** as one row: the
   result first, then a one-line reason. No separate decision documents.
4. **Speed is a feature.** Never re-parse a mod that hasn't changed; never
   block the window while working. See [decision 9](docs/decisions.md).
5. **Write docs plainly.** Short sentences, no unexplained jargon, results
   before reasoning. See [docs/README.md](docs/README.md#how-we-write-docs).

## Environment

Arch Linux dev container ([.devcontainer/](.devcontainer/)). Host paths are
mounted at the same paths inside, so `$STEAM_DIR`, `$PARADOX_DATA_DIR` and
`$STELLARIS_FRAMEWORK_DIR` are real on both sides. All packages come from
pacman: there is no venv and no pip.
