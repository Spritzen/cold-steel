# Where Stellaris keeps things

Checked against the real install on 2026-10-01 (game **v4.5.1 "Cygnus"**,
native Linux build). Paths are shown with the container's environment
variables, which are the same paths as on the host.

## Game install (read-only)

`$STEAM_DIR/steamapps/common/Stellaris/`

| Item | What it's for |
|---|---|
| `stellaris` | The game program |
| `launcher-settings.json` | Game version (`rawVersion`: `v4.5.1`) and the user data path (`gameDataPath`: `$LINUX_DATA_HOME/Paradox Interactive/Stellaris`) |
| `common/`, `events/`, `localisation/`, `gfx/`, `interface/`… | Vanilla game files. Mods override these |
| `dlc/` | One folder per DLC |

Stellaris is Steam app **281990**.

## Steam (read-only)

| Path | What it's for |
|---|---|
| `$STEAM_DIR/steamapps/libraryfolders.vdf` | Every Steam library folder, and which apps are in each. Start game discovery here |
| `$STEAM_DIR/steamapps/workshop/content/281990/<id>/` | One folder per subscribed Workshop mod, named by its Workshop ID |

Some Workshop mods are a single `.zip` instead of loose files (for example
mod `1224507727` holds only `exst.zip`). Their `descriptor.mod` is inside the
zip, and their picture is often missing. The launcher keeps its own copy of each
Steam thumbnail in `.launcher-cache/` (see `mods.thumbnailPath` below).

## Paradox user data (read-write)

`$PARADOX_DATA_DIR/Stellaris/`

| Path | What it's for |
|---|---|
| `mod/*.mod` | One descriptor per mod the game knows about. Workshop mods get `ugc_<id>.mod` |
| `mod/<folder>/` | Local mods (and, after Phase 6, links to our built mods) |
| `dlc_load.json` | **What the game actually loads**: `enabled_mods` (in order) and `disabled_dlcs` |
| `launcher-v2.sqlite` | The Paradox launcher's database: mods and playsets |
| `launcher-v2.cold-steel-orig.sqlite` | Our one-time original backup ([decision 6](../decisions.md)). Never overwrite |
| `logs/error.log` | Errors from the last game run. Older runs are kept as `error.log.<date>` |
| `logs/` (others) | `game.log`, `setup.log`, `script_documentation/`, and more |

### A `.mod` descriptor

```
name="Extended Soundtrack"
tags={
	"Sound"
}
picture="estn.jpg"
supported_version="2.1.*"
archive="/home/…/workshop/content/281990/1224507727/exst.zip"
remote_file_id="1224507727"
```

Loose-file mods use `path="…"` instead of `archive="…"`. Paths are absolute
host paths.

### `dlc_load.json`

```json
{"enabled_mods": ["mod/ugc_1623423360.mod", "mod/ugc_3090328185.mod"],
 "disabled_dlcs": ["dlc/dlc033_cosmic_storms/dlc033.dlc"]}
```

The order of `enabled_mods` is the load order.

### `launcher-v2.sqlite` tables we use

| Table | Holds |
|---|---|
| `mods` | Every known mod: `id` (the launcher's own ID), `steamId`, `name`, `displayName`, `version`, `requiredVersion`, `dirPath`, `archivePath`, `thumbnailPath`, `status` (`ready_to_play`, or `unsubscribed` for mods gone from disk) and more |
| `playsets` | `id`, `name`, `isActive`, `loadOrder` (`custom` or blank), `createdOn`, `updatedOn`, `isRemoved`… |
| `playsets_mods` | Which mods are in which playset: `playsetId`, `modId`, `enabled`, `position` |
| `playsets_dlcs` | DLC per playset |

The layout has changed between launcher versions, so check for the columns we
need on open and stop clearly if they're missing. On this install: 62 mods
(7 of them unsubscribed), 4 playsets. The file uses `journal_mode=delete`, so
opening it read-only creates no `-wal` or `-shm` files.
