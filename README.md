# unifold

[![tests](https://github.com/WIZ4RD-OM24/unifold/actions/workflows/tests.yml/badge.svg)](https://github.com/WIZ4RD-OM24/unifold/actions/workflows/tests.yml)

**Work on your Uniface codebase with the tools everyone else already uses.**

Uniface stores components, ProcScript and entity models as rows in a repository
database, not as files. Nothing else in your toolchain can see them. `unifold`
converts a Uniface export into a clean tree of plain text files — and converts
it back again, verifiably unchanged.

```
export  ──►  explode  ──►  edit / review / search  ──►  implode  ──►  import
```

Verified end to end against real exports from **Uniface 9.7, 10.2 and 10.4**.

---

## What this gives you

**Real version control.** Commit an exploded tree and `git diff` shows exactly
which trigger changed and how. Uniface exports are byte-stable, so a three-line
ProcScript change produces a three-line diff — not an unreadable XML blob.

**Code review that works.** Reviewers read `.proc` files in a pull request
instead of squinting at a repository dump. Blame, history and line comments all
behave normally.

**Search across the whole codebase.** `grep` finds every use of a field, a
message or an operation in seconds — across every component at once.

**Call tracing the IDE cannot do.** Find every caller of a library proc, see what
a component depends on, and identify entries nothing calls. These questions span
components, which is precisely where the IDE has no answer.

**AI assistance.** Point Claude Code, Copilot or any assistant at the exploded
tree and it can read, explain and modify your Uniface code like any other
project. This is the capability the platform otherwise has no route to.

**Readable form layouts and properties.** Packed values that display as
delimiter soup are decoded into structured key/value listings and a visual
sketch of the form.

**Migration confidence.** 9.7 and 10.x exports explode to the same shape, so a
component can be diffed across an upgrade to see precisely what moved.

---

## Requirements

Python 3.9 or later. No third-party dependencies.

## Install

```bash
py -3 -m pip install -e .
```

This provides the `unifold` command. To run without installing, prefix any
command with `PYTHONPATH=src py -3 -m unifold.cli` instead of `unifold`.

---

## Quick start

**1. Export a component from Uniface.** Either use *Export Repository Objects*
in the IDE — set **Object Name**, set **File Name** to a `.xml` path, leave
**Append** unticked — or do it from ProcScript:

```
vCount = $ude("export", "component", "MYCOMPONENT", "C:\temp\export.xml")
```

**2. Explode it.**

```bash
unifold explode C:/temp/export.xml C:/work/mycomponent
```

**3. Read it.**

```
C:/work/mycomponent/
  manifest.txt                        summary of what the export contained
  UFORM/MYCOMPONENT/
    properties.txt                    scalar columns, sorted
    USCRIPT.proc                      the component's ProcScript
    INIT.proc                         one file per code-bearing column
    FORMPIC.txt                       the form layout
    decoded.txt                       packed values, made readable
  UXGROUP/EMPLOYEE/properties.txt     entities used by the component
  UXFIELD/NAME/properties.txt         their fields
```

That directory is ordinary text. Commit it, grep it, review it, or point an AI
assistant at it.

---

## Commands

| Command | Purpose |
|---|---|
| `unifold explode FILE OUT/` | Convert an export into a readable source tree |
| `unifold implode TREE/ OUT.xml` | Rebuild an importable export from an edited tree |
| `unifold roundtrip FILE` | Verify an export survives explode → implode unchanged |
| `unifold xref WORKSPACE/` | Trace calls and dependencies across components |
| `unifold probe FILE` | Report an export's structure, when something looks wrong |
| `unifold compare A B` | Determine whether two exports genuinely differ |
| `unifold schemadiff A B` | Compare the schemas of two exports |

Run `unifold <command> --help` for full options.

### explode

```bash
unifold explode export.xml out/ [--force] [--dry-run]
```

Each repository row becomes a directory. Short values go into `properties.txt`;
multi-line or long values — the ProcScript — each get their own file.

Refuses to write into a non-empty directory unless given `--force`. `--dry-run`
lists what it would write without touching disk.

Two things to know:

- **Names stay as Uniface uses them.** Directories are `UXGROUP` and `UXFIELD`,
  not `entity` and `field`. Renaming would assert a mapping that has not been
  verified, and a confidently wrong name is worse than an unfamiliar correct one.
- **Content decides the split.** Whether a column becomes a file or a property
  line depends on its value, not on a list of known trigger names. `UFORM` alone
  has 69 columns and the set varies by table and version, so a fixed list would
  go stale.

### implode

```bash
unifold implode tree/ rebuilt.xml [--force]
```

Rebuilds an importable export from an edited tree. Edit the `.proc` files in any
editor first.

**`implode` never touches your repository.** It writes an XML file and stops.
Importing it is a separate action you take in the Uniface IDE. `unifold` has no
write access to anything of yours at any point.

### roundtrip

```bash
unifold roundtrip export.xml
```

Run this on your own exports **before** trusting `implode` with them. It
explodes, implodes and compares the two documents element by element and
attribute by attribute, with values checked byte for byte, in a scratch
directory that is then discarded.

```
VERDICT: faithful -- every element, attribute and value survived the round trip.
```

Exit code 0 means faithful. If it reports otherwise, do not import that export's
imploded output.

### xref

```bash
unifold xref workspace/ [--symbol NAME] [--unresolved] [--uncalled] [--json]
```

Explode several exports into one directory, then trace across all of them:

```bash
unifold xref C:/work/workspace --symbol OccurrenceSetFieldColors
```

```
Defined in 2 place(s)
  entry      HILIGHTROW_Include_Proc/USOURCE/HILIGHTROW/UTEXT.proc:1
  entry      cpt_showemployeeswithhighlight/UFORM/SHOWEMPLOYEES/USCRIPT.proc:20

Called from 7 place(s)
  call     bootstrap_model/UCGROUP/EMPLOYEE/UOCC_SCRIPT.proc:45
  call     cpt_showemployeeswithhighlight/UXGROUP/EMPLOYEE/UOCC_SCRIPT.proc:6
  call     prj_full_demoproject/UCGROUP/EMPLOYEE/UOCC_SCRIPT.proc:45
```

Without `--symbol` it summarises the workspace, reporting **names called but not
defined here** (what your code depends on and has not exported) and **entries
nothing calls** (dead-code candidates).

Triggers and operations are excluded from the dead-code list by design: the
runtime fires triggers and other components activate operations, so silence
inside a workspace proves nothing about them. Even an uncalled entry may be
reached from something you have not exported — confirm before deleting.

### probe, compare, schemadiff

Diagnostics, useful when an export behaves unexpectedly.

`probe` reports an export's actual element tree, attribute cardinalities and
where ProcScript lives. `compare` tells you whether two exports differ
genuinely, cosmetically, or only in ordering. `schemadiff` compares the schemas
of two exports, for instance across versions.

---

## Everyday workflows

### Put a codebase under version control

```bash
unifold explode C:/temp/all.xml C:/work/src --force
git -C C:/work/src add -A
git -C C:/work/src commit -m "Snapshot"
```

Re-export and re-explode over the top whenever you want; `git diff` then shows
exactly what changed. A bulk export (`$ude("export", "component", "*", ...)`)
puts an entire subsystem under review at once.

### Edit outside the IDE

```bash
unifold roundtrip C:/temp/export.xml     # confirm fidelity first
unifold explode  C:/temp/export.xml C:/work/tree
#   ... edit the .proc files ...
unifold implode  C:/work/tree C:/temp/changed.xml
#   ... import changed.xml from the Uniface IDE ...
```

### Understand unfamiliar code

```bash
unifold xref C:/work/src --symbol SOME_LIBRARY_PROC
grep -rn "SOME_MESSAGE" C:/work/src
```

---

## Decoded views

Uniface packs structure into delimited strings that are unreadable as stored:

```
FORMPIC: <uFRM>TYP=F<uSEP>NAM=LASTNAME<uSEP>WID=28<uSEP>HEI=1<uFRM>
WINPROP: CAPTION=<uSEP>CANRESIZE=<uSEP>MODAL=T<uSEP>SPLIT=
```

`decoded.txt` renders these. Form layouts become a sketch, each field padded to
its declared width so the proportions survive:

```
  [EMPLOYEE.BOOTSTRAP]
   [NAME]     [LASTNAME]           [BIRTHDATE]  [AGE]   [ROLE]      [EMAIL]

  TYP  NAM                 WID  HEI  HOC  VOC
  E    EMPLOYEE.BOOTSTRAP  157  26   157  2
  F    LASTNAME            28   1    -    -
```

Packed properties become plain key/value lines:

```
  MODAL      = T
  CANRESIZE  = (empty)
```

These views are **display-only**. `implode` reads the raw values and ignores
`decoded.txt`, so decoding cannot affect fidelity. Editing a decoded view has no
effect; change the underlying column instead.

### Separator markers

Exports reference a `UNIFACE.DTD` that Uniface does not ship alongside them, so
separator characters appear as entities. `unifold` shows them as visible
markers:

| Marker | Meaning |
|---|---|
| `[[uSEP]]` | List entry separator |
| `[[uNOT]]` | Nested-list marker |
| `[[uFRM]]` | Widget descriptor delimiter in form layouts |

These are more readable than the originals, which are non-printing characters
that vanish when copied. `implode` converts them back to real entity references,
so you can type `[[uSEP]]` when writing new code and it will import correctly.

---

## Safety

- **Read-only with respect to Uniface.** `unifold` reads and writes files. It
  never connects to, reads from or writes to a repository.
- **Importing is always your decision**, taken in the IDE.
- **Fidelity is verifiable, not asserted.** `roundtrip` proves reproduction on
  your own exports before you rely on it.
- **No silent overwrites.** `explode` and `implode` refuse to clobber existing
  output without `--force`.
- **Missing input is reported, not ignored.** If `implode` cannot find a file it
  expects, it warns and exits non-zero rather than quietly writing an empty
  value.

---

## Compatibility

| Version | Status |
|---|---|
| Uniface 9.7 | Verified against a real export |
| Uniface 10.2 | Verified against real exports |
| Uniface 10.4 | Verified against a real export |

All versions share the same export container and are handled by one code path,
so other 9.x and 10.x releases are very likely to work. TRX files are not
supported — they are Rocket's obsolete pre-Uniface-8 format. Select **XML** when
exporting.

## Limitations

- **Uniface's importer has not been tested with imploded output.** `roundtrip`
  proves the document is reproduced faithfully, but no one has yet confirmed
  Uniface accepts it. Try a throwaway component in a repository you can afford
  to break before relying on this.
- **No repository connection.** Exporting and importing are manual steps.
- **`xref` recognises `call` and `activate`.** Dynamic dispatch and indirect
  invocation are not tracked.
- **Names are Uniface's**, not friendlier aliases.

---

## Project layout

```
src/unifold/explode.py    export XML -> readable source tree
src/unifold/implode.py    readable tree -> export XML, plus fidelity checking
src/unifold/xref.py       cross-component call graph
src/unifold/packed.py     decoding packed lists and form layouts
src/unifold/probe.py      structure discovery and content classification
src/unifold/compare.py    export stability measurement
src/unifold/schemadiff.py schema comparison between exports
src/unifold/cli.py        command line
docs/FORMAT-NOTES.md      the export format as measured, and what is still open
scripts/fetch_samples.py  downloads real public exports into samples/
```

## Development

```bash
py -3 scripts/fetch_samples.py      # real exports published by Rocket
py -3 -m unittest discover -s tests
```

Tests that need real exports skip cleanly when `samples/` is absent, so a fresh
clone passes without network access.

Fixtures under `tests/fixtures/` are **synthetic** — invented to exercise code
paths. They are not evidence about the real format and should not be read as
such. [docs/FORMAT-NOTES.md](docs/FORMAT-NOTES.md) records what has actually
been measured, with sources, along with the open questions.

Tests run automatically on Windows and Linux against Python 3.9 and 3.13.

## Licence

MIT — see [LICENSE](LICENSE).
