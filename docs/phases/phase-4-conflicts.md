# Phase 4: Conflicts

**Status:** Not started
**Result:** For any playset, you can see exactly where mods clash, which mod
currently wins each clash, and the clashing versions side by side.

This is the biggest phase, and the one where speed matters most.

## Done when

- [ ] A conflict list for the playset, grouped by type (ships, events,
      technologies…) and by mod
- [ ] Each conflict shows which mod wins right now, and why (load order or
      game rule)
- [ ] A side-by-side viewer shows each version, with the differences
      highlighted
- [ ] Filters: by mod, by type, and "only conflicts involving this mod"
- [ ] Search: find any game object by name across the whole playset
- [ ] Speed, measured with `make bench` on your real 56-mod install:
      first full scan under 30 s; re-scan after changing one mod under 2 s;
      the window never freezes
- [ ] The winner rules are tested against what the game actually does (a
      live run for each rule type)

## What it contains

**Two kinds of conflict**
- **File conflicts:** two mods ship a file at the same path. The later
  mod's file replaces the earlier one entirely. This is cheap to find, since it
  only needs file names.
- **Object conflicts:** two mods define the same game object (for example
  the same technology name) in *different* files. Finding these means reading
  inside the files, so it needs the parser.

**Winner rules.** Who wins depends on the folder. For most things the last
loaded wins, but some types keep the *first* one loaded, and for files in the
same folder, file names decide the order. These rules go in one data table,
one row per game folder, so they're easy to check and fix.

**The script parser.** It reads Paradox script (`key = value`, `{ }`
blocks) into names and positions. Built for speed:
- A regex-based tokenizer: the heavy work happens in C inside Python's `re`
- Parses mods in parallel across CPU cores
- Results cached per file by hash, so an unchanged file is never parsed twice
- If it's still too slow: compile it with mypyc, or swap in a Rust parser
  (`jomini`) as a last resort ([decision 9](../decisions.md))

**Conflict index.** A table of "object name → which mods define it, in which
file, at which line". Built once and updated per changed mod. This also
powers the search box.

## Borrowed from

- Irony: the conflict solver layout, the conflict types it tracks, and its
  side-by-side viewer.
- STG `vendor.py` / `.docs/architecture/vendored-merge.md`: what we learned
  about how Stellaris resolves overlapping files.

## Open questions

| Question | Recommendation |
|---|---|
| Use an existing Python parser for Paradox script? | Check PyPI at the start of this phase. Use one only if it's maintained, handles Stellaris syntax, and is fast. Otherwise write our own |
| Where do the winner rules come from? | Start from Irony's published rules (MIT license), check them against the game, and correct them where live runs disagree |
| Include graphics, sound and other non-script files? | File conflicts only. No object-level parsing for those |
