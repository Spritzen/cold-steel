# Decisions

Every settled decision, one row each. **The "Decided" column is always the
current answer.** If a decision changes, edit its row and add the date to
"Changed". Don't add a new row that contradicts an old one.

Open questions don't go here. They live in the phase they block
([phases/](phases/README.md)), and move here once answered.

| # | Decided | Why | Date | Changed |
|---|---|---|---|---|
| 1 | Written in **Python, with Qt 6 (PySide6)** for the GUI | Quickest to build with, widely used, looks native on KDE, and reuses our Python tools from STG | 2026-10-01 | |
| 2 | **Every package comes from Arch's official repos.** No venv, no pip | We develop against exactly what users install, and the Arch package's dependency list stays accurate | 2026-10-01 | |
| 3 | **mypy is the one type checker, ruff lints and formats.** Pylance only does editor completion | Each error is reported once. mypy also provides `mypyc` for compiling slow code later | 2026-10-01 | |
| 4 | **Stellaris only** | Keeps every feature focused. Irony already covers the other games | 2026-10-01 | |
| 5 | **Develop in an Arch dev container that mounts host paths at the same place** | Game and mod discovery behaves exactly as it will on the host, and Claude sessions carry across | 2026-10-01 | |
| 6 | **Never write a Paradox file without backing it up first.** A one-time original copy, `launcher-v2.cold-steel-orig.sqlite`, is never overwritten | Playsets live in that database, and losing them is the worst bug this app could have | 2026-10-01 | |
| 7 | **Take four features from STG:** playset merge, health checks, error log reader, snapshots + symlink deploy | They're proven in STG and none of them are in Irony. See [from-stg.md](reference/from-stg.md) | 2026-10-01 | |
| 8 | **Decisions are rows in this file**, not separate documents | STG's 111 decision files made the current answer hard to find | 2026-10-01 | |
| 9 | **Speed rules:** never re-parse an unchanged file (check timestamp, then xxhash); cache with msgspec; parse in parallel; never block the window. Compile with mypyc only if profiling shows the need | Large playsets are where Irony feels slow, and the cache shapes the design so it has to be there from the start | 2026-10-01 | |
