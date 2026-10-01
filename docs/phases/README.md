# Phases

Each phase ends with something you can use. Work goes in order: each phase
relies on the one before.

| Phase | Result | Status |
|---|---|---|
| [0 — Foundation](phase-0-foundation.md) | An empty window opens, and one command runs every check | Done |
| [1 — Discover](phase-1-discover.md) | The app lists every installed mod and your launcher playsets. Read-only | Done |
| [2 — Playsets](phase-2-playsets.md) | Build a playset in the app and launch the game with it | Not started |
| [3 — Diagnose](phase-3-diagnose.md) | See broken mods before playing, and which mod caused each error after | Not started |
| [4 — Conflicts](phase-4-conflicts.md) | See exactly where mods clash, side by side | Not started |
| [5 — Resolve](phase-5-resolve.md) | Fix clashes and save the fixes as a patch mod | Not started |
| [6 — Build](phase-6-build.md) | Pin mod versions and merge a playset into one standalone mod | Not started |
| [7 — Release](phase-7-release.md) | Install it on any Arch system with pacman | Not started |

## How a phase file works

- **Done when:** the checklist that ends the phase. Tick items as they're
  shown working, not when the code is written.
- **What it contains:** the features and the parts behind them.
- **Borrowed from:** what we take from Irony or STG.
- **Open questions:** each one has a recommended answer. When it's answered,
  delete it here and add a row to [decisions.md](../decisions.md).

Update the status column above when a phase starts or finishes.
