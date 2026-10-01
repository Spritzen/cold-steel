# Cold Steel

A mod manager for **Stellaris** that runs natively on Linux.

Cold Steel shows every mod you have installed and the playsets you made in the
Paradox launcher, all in one fast window. It's built for the native Linux
version of the game, so there's no Wine or Proton involved.

> **Early days.** Cold Steel can build playsets, start the game with them,
> show you which mods are broken, and show and fix where mods clash. It backs
> up every Paradox file before it changes one.

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
- **Make and edit playsets.** Drag mods into load order, turn them on and
  off, choose DLC, then press **Play** to start the game with that playset.
  You can import playsets from the launcher and export them back.
- **Spots broken mods.** Each mod gets a health badge. Click it to see each
  problem, with the file and line: a localisation file the game will ignore,
  a line it will skip, a stray `}` that cuts a file short.
- **Shows which mod caused each error.** After you play, **Errors** reads the
  game's `error.log` and groups the errors by the mod they came from.
- **Shows where mods clash.** Press **Conflicts** to see every place two mods
  change the same file or the same thing in the game, like a technology, an
  event or a line of text. It tells you which mod wins and why, and shows the
  versions side by side with the differences highlighted. You can search for
  anything in the playset by name. The rules for who wins were checked
  against the game itself.
- **Fixes clashes.** For each clash, pick the version you want, keep the one
  that wins now, or write your own. Ignore the ones you don't care about.
  **Generate patch mod** turns your choices into a small mod that loads last,
  so the game uses them. If a mod updates a thing you chose for, Cold Steel
  asks you to look at it again.
- **Search and filter.** By name or tag, outdated mods only, mods with
  problems, or mods that are or aren't in a playset.
- **Opens quickly.** It remembers what it has already read, so with 50+ mods
  the list appears in about half a second.

## Coming next

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

Cold Steel never changes Steam's folders or your mods. It changes Paradox's
files only when you ask: **Play** writes `dlc_load.json` (and a `.mod` file
for a mod the launcher hasn't seen yet), and **Export to launcher** writes the
launcher's playsets. Each file is backed up first, and
nothing is written while the launcher or the game is open. **Generate patch
mod** adds two things of its own to the game's `mod` folder: a link to the
patch, and its `.mod` file. Deleting the playset removes them.

| Folder | What's in it |
|---|---|
| `~/.config/cold-steel/` | Your settings |
| `~/.local/share/cold-steel/` | Your playsets, your choices for each playset's clashes, the patch mods, and backups of the Paradox files |
| `~/.cache/cold-steel/` | What it remembers about your mods, so it opens fast. Safe to delete |

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

## Add a desktop icon

To start Cold Steel from your desktop or app menu instead of a terminal, run
these commands inside the `cold-steel` folder.

These steps are for **Arch Linux with the KDE Plasma desktop** (tested on
CachyOS with Plasma). They use the standard Linux desktop file format, so
GNOME, Xfce and other desktops should work too, but we haven't tested them.

```sh
install -Dm644 src/cold_steel/data/cold-steel.svg \
  ~/.local/share/icons/hicolor/scalable/apps/cold-steel.svg

mkdir -p ~/.local/share/applications
cat > ~/.local/share/applications/cold-steel.desktop <<EOF
[Desktop Entry]
Type=Application
Name=Cold Steel
GenericName=Stellaris Mod Manager
Comment=Manage Stellaris mods and playsets
Exec=env PYTHONPATH=$PWD/src python3 -m cold_steel
Path=$PWD
Icon=cold-steel
Terminal=false
Categories=Game;
StartupWMClass=cold-steel
EOF

install -Dm755 ~/.local/share/applications/cold-steel.desktop ~/Desktop/cold-steel.desktop
```

Cold Steel now shows up under **Games** in your app menu and as an icon on
your desktop. The first time you double-click the desktop icon, your desktop
may ask whether to trust it. Choose **Allow launching**.

If you move the `cold-steel` folder later, run the commands again from its
new place.

## Licence

[MIT](LICENSE).

Cold Steel is inspired by
[IronyModManager](https://github.com/bcssov/IronyModManager), which was the
manager I used for years on Windows. Big props to Mario!