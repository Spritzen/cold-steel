# Scope

**Cold Steel is a native Linux mod manager for Stellaris.** It lets you
organise your mods into playsets, see where mods clash, fix those clashes, and
launch the game. It can also merge a whole playset into one standalone mod.

It's modelled on [IronyModManager](https://github.com/bcssov/IronyModManager)
(Irony for short), a Windows-first manager written in C#. Cold Steel does the
same core jobs, built for Linux and Qt, and adds four features taken from our
Stellaris mod project ([from-stg.md](reference/from-stg.md)).

## Who it's for

Stellaris players and modders on Arch-based Linux, playing the native Linux
build of the game through Steam.

## What it does

Each feature lists the phase that delivers it.

| Feature | In plain terms | Phase |
|---|---|---|
| Find the game and mods | Locates Stellaris, every Workshop mod and every local mod with no setup | [1](phases/phase-1-discover.md) |
| Read existing playsets | Shows the playsets you already made in the Paradox launcher | [1](phases/phase-1-discover.md) |
| Playsets and load order | Create, copy, rename and delete playsets; turn mods on and off; drag to reorder | [2](phases/phase-2-playsets.md) |
| Launch the game | Start Stellaris with the chosen playset, skipping the Paradox launcher | [2](phases/phase-2-playsets.md) |
| Sync with the launcher | Import playsets from the launcher, and push ours back to it | [2](phases/phase-2-playsets.md) |
| Mod health checks | Flags broken files in a mod before you play (from STG) | [3](phases/phase-3-diagnose.md) |
| Error log reader | After a game run, shows which mod caused each error (from STG) | [3](phases/phase-3-diagnose.md) |
| Conflict finder | Shows which mods change the same file or the same game object | [4](phases/phase-4-conflicts.md) |
| Conflict viewer | Side-by-side view of the clashing versions | [4](phases/phase-4-conflicts.md) |
| Conflict resolution | Pick a winner, ignore a conflict, or write your own fix | [5](phases/phase-5-resolve.md) |
| Patch mod | Saves your resolutions as a small mod that loads last | [5](phases/phase-5-resolve.md) |
| Source snapshots | Pins exact copies of Workshop mods so a Steam update can't silently change a playset (from STG) | [6](phases/phase-6-build.md) |
| Merge to one mod | Combines a whole playset into one standalone mod (from STG) | [6](phases/phase-6-build.md) |
| Symlink deploy | Puts a built mod in the game's mod folder as a link, not a copy (from STG) | [6](phases/phase-6-build.md) |
| Arch package | Installable with pacman / from the AUR | [7](phases/phase-7-release.md) |

## How it improves on Irony

- **Native Linux.** Uses the Linux build of the game and Linux paths, with no
  Wine, no Proton and no Windows assumptions.
- **Fast on large playsets.** Unchanged mods are never re-read, work runs in
  the background, and parsing uses every CPU core.
  ([decision 9](decisions.md))
- **Snapshots.** Irony always reads whatever Steam last downloaded. Cold Steel
  can pin a playset to exact mod versions.
- **Diagnosis.** Health checks and error-log reading aren't in Irony.
- **Safe with your data.** Backs up the launcher database before every write.

## What it won't do

- **Other games.** Stellaris only ([decision 4](decisions.md)). Irony covers
  that need.
- **Windows or macOS.**
- **Subscribe to or download Workshop mods.** That stays in the Steam client.
- **Edit mods in general.** The only files it writes are playsets, the patch
  mod, built mods and its own data.
- **Replace CWTools.** Deep checks of script meaning stay in the editor.
  Cold Steel's health checks only catch file-level breakage.
