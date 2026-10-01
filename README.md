# Cold Steel

A mod manager for **Stellaris** that runs natively on Linux.

Cold Steel shows every mod you have installed and the playsets you made in the
Paradox launcher, all in one fast window. It's built for the native Linux
version of the game, so there's no Wine or Proton involved.

> **Early days.** Cold Steel can show your mods and playsets, but it can't
> change them yet. It only reads your files, so it's safe to try alongside
> the Paradox launcher.

## What it does today

- **Finds Stellaris on its own.** It looks through your Steam libraries, even
  ones on other drives, and shows the game version.
- **Lists all your mods.** Workshop mods and local mods, with their picture,
  version, tags, and the game version each one was made for.
- **Flags outdated mods.** Mods made for an older version of Stellaris are
  shown in red.
- **Shows your playsets.** The playsets from the Paradox launcher, in the same
  order, with each playset's mods in load order. It also tells you if a
  playset includes a mod you've since unsubscribed from.
- **Search and filter.** By name or tag, outdated mods only, or mods that
  are or aren't in a playset.
- **Opens quickly.** It remembers what it has already read, so with 50+ mods
  the list appears in about half a second.

## Coming next

- Make and edit playsets, and start the game with one
- Spot broken mods before you play, and see which mod caused an error after
- See where mods clash, and fix the clashes with a small patch mod
- Pin mod versions, and merge a whole playset into one mod
- An Arch package you can install with pacman

## What it won't do

- **Download mods.** Subscribe to mods in Steam as usual. Cold Steel picks
  them up from there.
- **Run other Paradox games.** It's for Stellaris only.
- **Run on Windows or macOS.**

## Try it

You need **Arch Linux** (or an Arch-based distro), with Stellaris installed
through Steam. There's no package yet, so you run it from the source code.

1. Install what it needs:

   ```sh
   sudo pacman -S --needed git python pyside6 qt6-wayland python-msgspec python-xxhash
   ```

2. Download Cold Steel and start it:

   ```sh
   git clone https://github.com/Spritzen/cold-steel.git
   cd cold-steel
   make run
   ```

### If it can't find Stellaris

Cold Steel looks for Steam in the usual place (`~/.local/share/Steam`). If you
installed Steam as a Flatpak or Snap, or somewhere else, choose
**File › Set Steam folder…** and pick the folder that holds `steamapps`.

Cold Steel needs the native Linux version of Stellaris. If Steam Play
(Proton) is turned on for Stellaris, turn it off in the game's Steam
properties.

## Your files are safe

Cold Steel never changes Steam's folders, your mods, or the Paradox launcher's
files. It only writes its own files:

| Folder | What's in it |
|---|---|
| `~/.config/cold-steel/` | Your settings |
| `~/.cache/cold-steel/` | What it remembers about your mods, so it opens fast. Safe to delete |

When playset editing arrives, Cold Steel will back up the launcher's playsets
before it changes anything.

## For developers

Install the test and lint tools too:

```sh
sudo pacman -S --needed python-pytest python-pytest-qt python-pytest-benchmark ruff mypy
```

| Command | Does |
|---|---|
| `make check` | Lint, type-check and test. Run it before every commit |
| `make test` | Just the tests |
| `make bench` | Just the timing tests |
| `make format` | Fix formatting |

The tests use a small fake Stellaris install in `tests/fixtures/`, never your
own.

## Licence

[MIT](LICENSE). Cold Steel is inspired by
[IronyModManager](https://github.com/bcssov/IronyModManager), which does the
same job on Windows.
