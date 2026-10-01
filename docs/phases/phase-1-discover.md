# Phase 1: Discover

**Status:** Not started
**Result:** The app lists every installed mod and your existing launcher
playsets, without writing anything.

## Done when

- [ ] It finds Stellaris with no setup, and shows the game version
- [ ] It lists every Workshop mod and local mod, with name, version, supported
      game version, tags and thumbnail
- [ ] Mods made for an older game version are marked as outdated
- [ ] The playsets from the Paradox launcher are shown, in the launcher's order
- [ ] The mod list can be searched and filtered (by name, tag, outdated, in a
      playset or not)
- [ ] Opening the app a second time takes under 1 second with 50+ mods,
      because unchanged mods come from the cache
- [ ] Tests run against small sample mods in `tests/fixtures/`, not your real
      install
- [ ] Nothing under `$PARADOX_DATA_DIR` or `$STEAM_DIR` has changed (a test
      checks this)

## What it contains

**Finding the game.** Read `steamapps/libraryfolders.vdf` to get every Steam
library, then find app `281990` (Stellaris). The game version comes from
`launcher-settings.json`. Details:
[stellaris-files.md](../reference/stellaris-files.md).

**Finding mods.**
- Workshop mods: `steamapps/workshop/content/281990/<id>/`
- Local mods and `.mod` descriptor files: `$PARADOX_DATA_DIR/Stellaris/mod/`
- A reader for Paradox's descriptor format (`descriptor.mod` and `*.mod`):
  the first, small use of the script parser that Phase 4 grows.

**Reading launcher playsets.** Open `launcher-v2.sqlite` **read-only** and read
the `playsets`, `playsets_mods` and `mods` tables.

**The cache.** For each mod, remember file sizes, timestamps and xxhash
values, saved with msgspec. On the next start, only mods whose files changed
are re-read ([decision 9](../decisions.md)).

**The window.** Mod list (table view with thumbnails), search box, filters,
playset sidebar, and a status bar showing background progress.

## Borrowed from

- Irony: the mod list layout, filters and outdated marking.
- STG: `deploy.py` already reads `launcher-settings.json` to find the game data
  path. Reuse that logic.

## Open questions

| Question | Recommendation |
|---|---|
| Steam installed as Flatpak or Snap (different paths)? | Support the normal install only. Make the Steam path a setting so others can point it manually |
| Mods shipped as a `.zip` (`archive=` in the `.mod` file)? Some Workshop mods do this | Read inside the zip without unpacking it. Python's `zipfile` can do this |
