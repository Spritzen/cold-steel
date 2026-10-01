# Phase 5: Resolve

**Status:** Not started
**Result:** You fix clashes inside the app, and your fixes are saved as a small
patch mod that loads last and makes the game do what you chose.

## Done when

- [ ] For any conflict: pick the winning version, keep the current winner, or
      write a custom merged version in an editor
- [ ] "Ignore" a conflict (or every conflict of one mod or type) so it stops
      showing
- [ ] Resolutions are saved per playset and survive restarts and re-scans
- [ ] When a mod updates and changes a resolved object, that resolution is
      flagged "needs another look" instead of being silently kept
- [ ] "Generate patch mod" writes a mod containing only the resolved objects,
      adds it to the end of the playset, and the game loads it
- [ ] In-game, a test playset shows the chosen winners (a live run)
- [ ] Reset: clear one resolution, or all of them

## What it contains

**Resolutions store.** One file per playset in Cold Steel's data folder. It
records each conflict, what was chosen, and the hash of every version at that
moment. The hash is how "needs another look" works.

**Patch mod generator.** Writes a standard Stellaris mod: a descriptor plus
one file per resolved object, named so it wins under the Phase 4 winner rules.
It's generated, never hand-edited, so it can always be rebuilt from the
resolutions store.

**Merge editor.** A text editor next to the side-by-side viewer, for writing a
custom version. Run the Phase 3 brace check on what's typed before saving.

## Borrowed from

- Irony: conflict solver actions (resolve, ignore, custom), ignore rules, the
  generated patch mod, and resetting conflicts.
- STG: "never hand-edit a generated file". The patch mod is always rebuilt
  from saved choices.

## Open questions

| Question | Recommendation |
|---|---|
| Read Irony's saved resolutions so Irony users can switch? | Later, not in this phase. Note it for Phase 7 if anyone asks |
| Where does the patch mod live? | In Cold Steel's data folder, linked into the game's mod folder (the Phase 6 symlink method, pulled forward) |
