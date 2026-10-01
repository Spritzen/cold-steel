# What we take from Star Trek Galaxies (STG)

STG is our Stellaris mod project, mounted read-only at
`$STELLARIS_FRAMEWORK_DIR`. It builds one big mod out of 49 Workshop mods, and
its `tools/` folder solved several problems a mod manager has too.

**We rewrite, not copy.** STG's tools are tied to its own folders, `make`
targets and Star Trek content. We use them as a working specification: what to
check, what went wrong, and what the answer turned out to be. Their test cases
and the lessons in their comments are the valuable part.

| Cold Steel feature | STG source | What we keep | What changes | Phase |
|---|---|---|---|---|
| Mod health checks | `tools/validate.py` (7,853 lines) | The generic file checks: BOM, `:0` versions, brace balance, descriptor version | Most of the file is STG-specific (vendored files, its own `src/`), so we leave that out. Results show in the app, not a terminal | [3](../phases/phase-3-diagnose.md) |
| Error log reader | `tools/logs.py` | Which log files exist and which matter | Adds what STG didn't need: matching each error to the mod that caused it | [3](../phases/phase-3-diagnose.md) |
| Source snapshots | `tools/sources.py` | Pinned copies of Workshop mods, and showing what changed when Steam updates one | Per playset, and shared by content hash instead of one copy per project | [6](../phases/phase-6-build.md) |
| Merge to one mod | `tools/vendor.py` | Build in load order, record where every file came from | Driven by a playset + patch mod instead of a hand-written `vendor.yml` | [6](../phases/phase-6-build.md) |
| Symlink deploy | `tools/deploy.py` | Link, don't copy. Write paths the host-side game understands | Works for any built mod, not only STG | [6](../phases/phase-6-build.md) |

## Lessons STG paid for

- **A copied mod goes stale.** STG's deployed copy was five hours behind its
  build without anyone noticing. Links can't go stale.
- **Don't build from the live Workshop folder.** Steam changes it whenever an
  author updates, and deletes it when you unsubscribe. Hence snapshots.
- **Native Linux vs Proton paths.** Both user-data folders can exist on one
  disk, and only one is live. Ask `launcher-settings.json`, never guess.
- **Too many decision documents hide the answer.** Hence
  [decision 8](../decisions.md).
