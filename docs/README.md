# Documentation map

| File | What it answers |
|---|---|
| [scope.md](scope.md) | What Cold Steel is, what it does, what it won't do |
| [decisions.md](decisions.md) | Every settled decision, one row each, result first |
| [phases/](phases/README.md) | The build plan: one file per phase, each with a "done when" checklist |
| [reference/stellaris-files.md](reference/stellaris-files.md) | Where Stellaris, Steam and the launcher keep things, and what's inside |
| [reference/from-stg.md](reference/from-stg.md) | The four tools we take from the Stellaris mod project, and what changes |
| [architecture/](architecture/README.md) | How the code is laid out, how background work runs, where our data lives |
| [development.md](development.md) | How to run, test and lint |

New folders get added here when a phase needs them. Don't create a folder until it has something in
it.

## How we write docs

- **Result first.** Open with what is true or what was decided. Reasons come
  after, and are short.
- **Plain words.** Short sentences. If a term needs explaining, explain it the
  first time or link to where it's explained.
- **One place per fact.** If something is written down in one doc, other docs
  link to it instead of repeating it.
- **Decisions are rows, not documents.** Add a row to
  [decisions.md](decisions.md). If a decision changes, edit the row so it
  always shows the current answer, and note the date it changed.
- **Open questions live in the phase they block**, each with a recommended
  answer. Once answered, the question is deleted there and becomes a row in
  decisions.md.
- **Phase files keep "Done when" at the top**, as a checklist of things a
  person can see working, not a list of tasks.
