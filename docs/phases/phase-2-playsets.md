# Phase 2: Playsets

**Status:** Not started
**Result:** You build a playset in Cold Steel, press Play, and Stellaris starts
with exactly those mods in exactly that order.

This is the first phase that **writes** Paradox files, so the backup rule
([decision 6](../decisions.md)) starts to matter here.

## Done when

- [ ] Create, copy, rename and delete playsets
- [ ] Turn mods on and off, and drag them to set the load order
- [ ] "Sort" puts a playset into a sensible default order (rules below)
- [ ] Missing mods (unsubscribed since) are shown clearly, not silently dropped
- [ ] Import a playset from the Paradox launcher
- [ ] Export a playset back to the launcher, so the launcher sees it
- [ ] Press Play: the game starts, and in-game the mod list matches the
      playset exactly
- [ ] DLC can be turned on and off per playset
- [ ] Every write to `launcher-v2.sqlite` or `dlc_load.json` makes a dated
      backup first, and a test proves it
- [ ] Share a playset as a file, and load one from a file

## What it contains

**Our own playset storage.** Playsets live in Cold Steel's data folder, not
only in the launcher database. The launcher's database layout has changed
between launcher versions, so we treat it as something to sync with, not
something to depend on.

**What the game actually reads.** When Stellaris starts, it loads the mods
listed in `dlc_load.json`, in that order, and skips the DLC listed there.
Writing this file correctly is the core of "Play".

**Load order.** Mods load top to bottom, and later mods win. "Sort" uses:
1. Each mod's declared dependencies (load after what it depends on)
2. Known "load last" mods (patches, UI overhauls). Rules kept in one data file
3. Otherwise, keep the user's order

**Launcher sync.** Import reads the launcher tables. Export writes them back,
after a backup, and only while the launcher isn't running.

**Play button.** Writes `dlc_load.json`, then starts the game.

## Borrowed from

- Irony: collections (Irony's name for playsets), import/export formats, the
  play button that skips the launcher.
- STG: the playset backup (`launcher-v2.cold-steel-orig.sqlite`) is already
  made by the container's setup script.

## Open questions

| Question | Recommendation |
|---|---|
| How to start the game so the Paradox launcher doesn't open? | Test both ways in this phase: running the `stellaris` file directly (with Steam running), and through Steam with launch options. Keep whichever loads `dlc_load.json` reliably |
| Detect that the launcher is running before writing its database? | Yes. Refuse to write while it's open, and say why |
| Which format for shared playset files? | Plain JSON: the playset name plus each mod's Workshop ID and order. Also read Irony's export format so Irony users can switch |
