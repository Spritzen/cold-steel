# Cold Steel

A mod manager for **Stellaris** that runs natively on Linux (in all honesty fork the project and you can probably get Claude to rework for windows pretty easily; just ask it to plan it out with docs first and you can keep an eye on it).

Cold Steel shows every mod you have installed and your playsets, all in one
fast window. It shows which mods are broken and where mods clash, helps you
fix the clashes, and starts the game. It's built for the native Linux version
of the game, so there's no Wine or Proton involved.

![The main window: playsets on the left, the mods in the chosen playset on the right](screenshots/main-dark.png)

## Install

You need **Arch Linux** (or an Arch-based distro such as CachyOS, EndeavourOS
or Manjaro), with Stellaris installed through Steam.

1. Download the package, `cold-steel-<version>-1-any.pkg.tar.zst`, from the
   [latest release](https://github.com/Spritzen/cold-steel/releases/latest).
2. Install it with pacman, from the folder you downloaded it to:

   ```sh
   sudo pacman -U ./cold-steel-*.pkg.tar.zst
   ```

   pacman also installs what Cold Steel needs, such as PySide6, from Arch's
   own repos.

Then start **Cold Steel** from your app menu (it's under Games), or run
`cold-steel` in a terminal.

**To update,** download the new release's package and install it the same
way. Your settings and playsets are kept.

**To remove it,** run `sudo pacman -R cold-steel`. Your playsets and settings
stay in the folders listed under [Your files are safe](#your-files-are-safe).
Delete those too if you want everything gone.

### Build the package yourself

If you'd rather not install a downloaded package, build it from the release's
source code:

```sh
sudo pacman -S --needed git base-devel
git clone https://github.com/Spritzen/cold-steel.git
cd cold-steel/packaging
makepkg -si
```

### Run from source

To try the latest code instead:

```sh
sudo pacman -S --needed git python pyside6 qt6-wayland python-msgspec python-xxhash
git clone https://github.com/Spritzen/cold-steel.git
cd cold-steel
make run
```

## First start

The first time it opens, Cold Steel shows what it reads and what it changes.
Then it finds Stellaris through your Steam libraries, even ones on other
drives, and lists your mods. It copies in the playsets you made in the
Paradox launcher.

**If it can't find Stellaris:** open **File › Settings** and choose your Steam
folder (the one holding `steamapps`). You'll need this if Steam is a Flatpak
or Snap, or somewhere unusual.

**Cold Steel needs the native Linux version of Stellaris.** If Steam Play
(Proton) is turned on for Stellaris, turn it off in the game's Steam
properties.

## Using it

### Playsets

A playset is a list of mods, in the order the game loads them. Pick one on the
left to see its mods. Then you can:

- **Turn mods on and off** with the tick box, and **drag** them into order.
- **Add mods** from **All mods**: select them, right-click, **Add to playset**.
  To see only the mods a playset doesn't have yet, choose it in the filter
  above the list (**Exclude** is picked for you).
- **Sort** puts mods that others depend on first, then follows common load-order
  rules.
- **DLC…** chooses which DLC the playset loads.
- **▶ Play** starts Stellaris with the playset. The Paradox launcher isn't needed.
- **Delete a local mod**: right-click it, **Delete local mod…**. After you
  say yes, its `.mod` file and folder go to the trash. If the folder is a link,
  only the link goes. A folder outside the mod folder is left alone. The mod
  is also taken out of every playset that had it.
- **Your own mods**: a local mod you've uploaded to the Workshop shows as
  **Local (dev copy)** under **Source**, and the Workshop copy as **Workshop
  (your release)**. Both stay listed, so a playset can use either one.

The buttons under the list make, copy, rename and delete playsets. The
playset you last played is in bold.

### Find broken mods

Each mod has a **Health** column. **OK** means its files look fine. Click a
warning or error to see each problem, with the file and line: a localisation
file the game will ignore, a line it will skip, a stray `}` that cuts a file
short. Mods made for an older game version are shown in red under
**Made for**.

After you play, **Errors** reads the game's `error.log` and groups the errors
by the mod that caused them. When you start the game from Cold Steel, it reads
the log for you when the game closes.

### See and fix clashes

**Conflicts** shows every place two mods in the playset change the same thing:
a technology, an event, a line of text, or a whole file. It says which mod
wins and why, and shows the versions side by side with the differences
highlighted.

![The Conflicts window: the list of clashes, and two versions side by side](screenshots/conflicts-dark.png)

For each clash you can **use** either version, **keep the winner**, or
**write your own**. You can also **ignore** clashes you don't care about.
Then press **Generate patch mod**. Cold Steel writes your choices into a small
mod that loads last, so the game uses them. If a mod later changes something
you chose for, Cold Steel asks you to look at it again.

You can upload the patch mod to the Workshop and play that copy. Generating
the patch again still works, and keeps the Workshop id so the launcher can
update your upload.

The patch mod's thumbnail is the Cold Steel icon with a green sword, and it's
tagged **Fixes** (plus **Graphics** when it changes graphics), so it's ready
to upload as it is.

To delete the patch mod, right-click it in the mod list and choose **Delete
patch mod…**, or use **Playset › Delete patch mod…**. It's taken out of every
playset that has it. Your choices stay, so you can generate it again.

### Pin mod versions

A Steam update can change a mod halfway through a campaign. **Pins…** saves
a copy of the playset's Workshop mods as they are now, and plays those copies
until you say otherwise. When Steam has an update, Cold Steel tells you and
shows what changed. You can accept the update or keep your copy.

### Build one mod

**Build** merges the whole playset, with its patch mod, into one mod, and
makes a playset that plays just that mod. The build report shows where every
file came from. A built mod is for your own use only. It contains other
authors' work, so don't upload it.

### Saves

A save made with one set of mods can break when it's loaded with another, and
the game only warns when a mod's name is missing. Cold Steel ties each save to
the playset it belongs to, so it can tell you.

**This needs the game's cloud autosaves turned off.** The game sends
autosaves to Steam Cloud by default, and Cold Steel can't move those files.
Until you turn them off in the game's settings, none of this shows.
**Settings** says whether they're on.

![The Saves window: a playset's saves, one open to show its files](screenshots/saves-dark.png)

- **Saves…** lists the playset's saves, newest first. Open one to see its
  files, local and in Steam Cloud.
- A save you start with **▶ Play** belongs to that playset when the game
  closes. Older saves are listed as unbound, with the playset whose mods match
  them suggested. **Bind all suggested** takes those suggestions, and
  right-click moves a save to another playset or unbinds it.
- A save is marked ⚠ when the playset's mods changed since it was made, or its
  build or patch mod was made again, or it's from an older game version.
  Hover over the mark to see why.
- **Continue**, beside Play, plays the playset and opens its newest save,
  skipping the game's main menu. It asks first if the save is marked ⚠.
  Play points the game's own *Continue* at that save too.
- **Play and Continue keep other playsets' saves out of the game's Load
  menu**, so you can't load one with the wrong mods. They're put back when
  the game closes. Saves bound to no playset stay.
- **Build** on a playset that was built before asks what to do with the saves
  of the built playset: keep each one, or let it go. The local files of saves
  you let go are moved to the trash only if you tick that box.

### Empires

The game keeps every empire you design in one list, whatever mods you play
with. An empire made with a mod's planet class still shows in a playset
without that mod, and the game can change it there. In Cold Steel, each
playset has its own list of empires, and the game gets exactly that list.
Like saves, **this needs the game's cloud autosaves turned off.**

![The Empires window: a playset's empires, each marked with the mods it needs](screenshots/empires-dark.png)

- **Empires…** shows the playset's list. Each empire is marked ✓ when the
  playset has every mod it uses, or ⚠ with the mods it lacks.
- **▶ Play** and **Continue** give the game the playset's list. An empire you
  make, change or delete in game is kept in that list when the game closes,
  and no other playset sees the change.
- **Import from** copies empires from another playset's list. **Export to
  playset** copies them to another, and asks first if it lacks mods they use.
  An empire of the same name asks whether to replace it or skip.
- Empires that are in no playset's list are under **Not in any playset**:
  the ones you had before, and any made in a game started from the launcher.
  Import them into a playset, or delete them.
- A built playset uses the list of the playset it was built from.
- If a game update changes the empire file's format, Play won't give the game
  a list in the old one, and says why.

### Share playsets

- **Playset › Import from launcher** and **Export to launcher** copy playsets
  between Cold Steel and the Paradox launcher.
- **Open in launcher** exports the playset, makes it the launcher's selected
  playset, then opens the Paradox launcher.
- **File › Sync launcher** replaces all the launcher's playsets with Cold
  Steel's, after asking. Launcher playsets that aren't in Cold Steel are
  removed.
- When the launcher's copy of a playset differs from Cold Steel's, a line
  above the mod list says how. Starting it from the launcher would play that
  copy.
- **Save to file** and **Load from file** share a playset with a friend. The
  file uses the launcher's own format, and Irony Mod Manager's exports load
  too.

### Settings

**File › Settings** sets where Steam is, where Stellaris keeps its data, and
the theme. By default Cold Steel follows your desktop's light or dark theme.
It also shows whether the game's cloud autosaves are on, and where Cold
Steel keeps its own files.

![The Settings window](screenshots/settings-dark.png)

### Keyboard shortcuts

**Help › Keyboard shortcuts** (F1) lists them all. The main ones:

| Keys | Does |
|---|---|
| Ctrl+Enter | Play the selected playset |
| Ctrl+K | Conflicts |
| Ctrl+E | Errors from the last game |
| Ctrl+F | Search mods |
| Ctrl+N | New playset |
| Ctrl+D | Copy playset |
| F2 | Rename playset |
| Ctrl+L | Sort load order |
| Ctrl+B | Build one mod |
| F5 | Rescan mods |
| Ctrl+, | Settings |

## Your files are safe

Cold Steel never changes Steam's folders, your mods or your save files, and
never downloads anything. It changes Paradox's files only when you ask:

- **Play** writes `dlc_load.json`, which tells the game what to load. For a mod
  the launcher hasn't seen yet, it also adds the `.mod` file the game needs.
  With cloud autosaves off, it also writes `continue_game.json`, which names
  the save the game's *Continue* opens.
- **Play** and **Continue**, with cloud autosaves off, move the folders of
  other playsets' saves from `save games/` to `cold_steel_hidden_saves/`
  beside it while the game runs, then move them back. No file in them
  changes. If Cold Steel closes first, it moves them back when it next starts.
- **Play** and **Continue**, with cloud autosaves off, replace the game's
  empire file with the playset's list of empires, and copy it back into the
  list when the game closes. Each empire is copied byte for byte. If Cold
  Steel closes first, it copies it back when it next starts.
- **Export to launcher** and **Open in launcher** write the playset into the
  launcher's database. **Sync launcher** writes all of them, and removes the
  launcher's others.
- **Generate patch mod**, **Build** and **Pins** add their own mods to the
  game's `mod` folder: the patch and the build as links, a pinned copy as a
  `.mod` file. Their names start with `cold_steel_`. Deleting the playset
  removes them.
- **Delete local mod** moves a local mod's `.mod` file and folder to the
  trash, after asking.
- **Build**, when it rebuilds, moves the local files of saves you let go to the
  trash, only if you tick that box. Steam Cloud's copies are never touched.

Each Paradox file is backed up first, and nothing is written while the
launcher or the game is open.

| Folder | What's in it |
|---|---|
| `~/.config/cold-steel/` | Your settings |
| `~/.local/share/cold-steel/` | Your playsets, which playset each save belongs to, each playset's empires, conflict choices, patch mods, pinned copies, builds, and backups of the Paradox files |
| `~/.cache/cold-steel/` | What it remembers about your mods, so it opens fast. Safe to delete |

## What it won't do

- **Download mods.** Subscribe to mods in Steam as usual. Cold Steel picks
  them up from there.
- **Run other Paradox games.** It's for Stellaris only.
- **Run on Windows or macOS.**

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
| `make package` | Build the Arch package from the last commit, and check it with `namcap` |
| `make screenshots` | Retake the screenshots above from your own install |

The tests use a small fake Stellaris install in `tests/fixtures/`, never your
own. The PKGBUILD is in `packaging/`.

### A desktop icon when running from source

The package adds Cold Steel to your app menu. When running from source, you
can add a desktop icon named `cold-steel-dev` instead. It stays out of the app
menu, so it never clashes with an installed package.

Run these commands inside the `cold-steel` folder. They were tested on
CachyOS with KDE Plasma, and use the standard desktop file format, so other
desktops should work too.

```sh
install -Dm644 src/cold_steel/data/cold-steel.svg \
  ~/.local/share/icons/hicolor/scalable/apps/cold-steel.svg

cat > ~/Desktop/cold-steel-dev.desktop <<EOF
[Desktop Entry]
Type=Application
Name=cold-steel-dev
Comment=Cold Steel, run from source
Exec=env PYTHONPATH=$PWD/src python3 -m cold_steel
Path=$PWD
Icon=cold-steel
Terminal=false
EOF
chmod +x ~/Desktop/cold-steel-dev.desktop
```

If you move the `cold-steel` folder later, run the commands again from its
new place.

## Licence

[MIT](LICENSE).

Cold Steel is inspired by
[IronyModManager](https://github.com/bcssov/IronyModManager), which was the
manager I used for years on Windows. Big props to Mario!
