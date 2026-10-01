# Architecture

**The core never imports Qt.** Everything that finds, parses, compares or
builds mods is plain Python, so it can be tested and timed without a window.
A test ([test_layout.py](../../tests/test_layout.py)) fails if that breaks.

## Code layout

```
src/cold_steel/
├── __main__.py      entry point: python -m cold_steel
├── core/            plain Python: finding mods, parsing, conflicts, building
│   └── jobs.py      JobContext: progress reporting and cancelling for long jobs
├── store/           our own data, plus the parse cache
│   └── paths.py     where that data lives (XDG folders)
├── paradox/         reading and writing Paradox files (launcher DB, .mod, dlc_load.json)
└── ui/              Qt windows and widgets. Calls into core, never the other way
    ├── app.py       creates the QApplication and shows the window
    ├── main_window.py
    └── tasks.py     TaskRunner: runs core jobs off the main thread
tests/               pytest and pytest-qt tests
tools/               project scripts (check_links.py)
```

| Folder | May import |
|---|---|
| `core/` | standard library, msgspec, xxhash, `store/`, `paradox/` |
| `store/`, `paradox/` | standard library, msgspec, xxhash |
| `ui/` | anything |

## Background work

**Long jobs never run on the main thread** ([decision 9](../decisions.md)).

A job is a plain function in `core/` that takes a `JobContext` and returns a
result. It calls `ctx.progress(done, total, message)` as it goes. That call
also stops the job, by raising `Cancelled`, if someone has cancelled it.

The window starts a job with `TaskRunner.start(job)`, which returns a `Task`.
The task's signals arrive on the main thread:

| Signal | When |
|---|---|
| `progress(done, total, message)` | each time the job reports progress |
| `succeeded(result)` | the job returned |
| `failed(error)` | the job raised an exception |
| `cancelled()` | the job stopped after `task.cancel()` |
| `finished()` | always, last |

Closing the window cancels every running task and waits up to 5 seconds.

## Where our data lives

Set by [decision 11](../decisions.md). Each folder respects its `XDG_*`
variable if set.

| Folder | Holds |
|---|---|
| `~/.config/cold-steel/` | settings |
| `~/.local/share/cold-steel/` | playsets and patch-mod work |
| `~/.cache/cold-steel/` | the parse cache. Safe to delete |
