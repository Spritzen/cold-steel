# Phase 6: Build

**Status:** Not started
**Result:** You can pin a playset to exact mod versions, and merge a whole
playset into one standalone mod that plays the same as the playset.

These are the last two STG features ([from-stg.md](../reference/from-stg.md)).

## Done when

**Snapshots**
- [ ] "Pin" a playset: Cold Steel saves an exact copy of each of its Workshop
      mods
- [ ] When Steam updates a pinned mod, the app says so and shows what
      changed (files added, removed or changed) before you accept it
- [ ] A pinned playset still works after you unsubscribe from one of its mods
- [ ] Copies are shared between playsets and only take extra disk space for
      changed files

**Merge to one mod**
- [ ] "Build" turns a playset (plus its patch mod) into one standalone mod
- [ ] The built mod gives the same conflict winners as the playset (checked
      against the Phase 4 index)
- [ ] A build report lists where every file came from
- [ ] Re-building after one mod changes only redoes what changed

**Symlink deploy**
- [ ] Built mods and the patch mod appear in the game's mod folder as links,
      so a rebuild is live instantly with no copying
- [ ] The `.mod` file's paths are correct for the game running on the host

## What it contains

**Snapshot store.** Copies kept in Cold Steel's data folder, stored by
content hash so identical files are only kept once. A playset can point at
"live Workshop" (the default) or "pinned".

**Merge builder.** Copies files in load order so later mods win, applies the
patch mod last, and writes a new descriptor. Object-level conflicts were
already settled in Phase 5, so the build doesn't decide anything new. It just
carries out what the playset and patch mod say.

**Deploy.** Creates `mod/<name>` as a link to the built folder, and writes
`mod/<name>.mod` pointing at it.

## Borrowed from

- STG `sources.py`: pinned copies and diffing them against the Workshop.
- STG `vendor.py`: building one mod from many, with a record of where every
  file came from.
- STG `deploy.py`: link instead of copy (a copy went stale in STG and
  wasted a session), and writing host-correct paths.

## Open questions

| Question | Recommendation |
|---|---|
| Can a merged mod be uploaded to the Workshop? | Not a feature. It contains other authors' work. The build is for personal use, and the app says so |
| Store snapshots with hard links or copies? | Hard links within the same disk (no extra space), falling back to copies |
