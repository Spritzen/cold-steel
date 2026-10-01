# Phase 7: Release

**Status:** Not started
**Result:** Anyone on Arch can install Cold Steel with pacman (via the AUR),
and it looks and behaves like a finished app.

## Done when

- [ ] A `PKGBUILD` builds and installs cleanly in a fresh Arch container
- [ ] Its dependencies are exactly the pacman packages from
      [decision 2](../decisions.md), with no pip step
- [ ] Desktop entry and icon, so it appears in the app menu
- [ ] Settings window: Steam path, data folders, theme
- [ ] Follows the system light/dark theme
- [ ] Keyboard shortcuts for common actions, listed in the help
- [ ] First-run screen explains what the app will and won't touch
- [ ] User guide in the repo `README.md`, with screenshots
- [ ] Published to the AUR, and the GitHub repo made public

## What it contains

Packaging, polish and user docs. No new core features. Any feature that
turns up here gets its own phase instead.

## Borrowed from

- Irony: its AUR package is a reference for how others package a Paradox mod
  manager.

## Open questions

| Question | Recommendation |
|---|---|
| Licence? | MIT, the same as Irony, which also lets us use Irony's rules data |
| Compile slow modules with mypyc in the package? | Only if Phase 4 benchmarks needed it. Otherwise ship plain Python (simpler package) |
