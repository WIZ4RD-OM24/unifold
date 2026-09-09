# unifold

Read Uniface repository exports as human-readable, diffable source.

Uniface keeps component definitions, ProcScript and entity models as rows in a
DBMS repository, not as files. That single fact is why no modern editor, code
review tool, or AI coding assistant can be pointed at a Uniface codebase: there
is nothing on disk for them to read. The supported way out — XML export — gives
you text, but text shaped like a serialised repository transaction, not like
source code. A three-line ProcScript change produces a diff nobody can review.

`unifold` closes that gap: export XML in, a clean directory tree of `.proc` and
`.txt` files out, stable enough that git diffs are readable and Claude Code can
work on the result — and back again, verifiably unchanged.

Verified against real exports from **Uniface 9.7, 10.2 and 10.4**, all through
one code path. Read-only — `unifold` never writes to your repository.

The versions turned out to need no per-version handling at all. Every export —
9.7, 10.2, 10.4 — uses the same six-element container, and the format describes
its own schema inline, so one code path reads them all. Measurement replaced
what was going to be a mapping layer:

```
9.7 / 10.2 / 10.4 export ─► self-describing reader ─► same output tree
```

Because every version explodes to the same tree, a component can also be diffed
across a 9.7 → 10.x migration, which is otherwise painful to verify.

## Status

**Phase 0 — shipped.** Format discovery and stability measurement.
**Phase 1 — shipped.** `explode` turns real exports into readable source.
**Phase 2 — shipped.** `implode` rebuilds exports; `roundtrip` proves fidelity.
**Phase 3 — shipped.** Packed lists decoded, including a form layout sketch.
**Phase 4 — shipped.** `xref` traces calls across components.
**Phase 5 — next.** Semantic naming; a navigable workspace index; MCP server.

| Command | Does | Ready |
|---|---|---|
| `unifold probe FILE` | Reports an export's real element tree, attribute cardinalities, and where ProcScript and encoded blobs live | yes |
| `unifold compare A B` | Tells you whether two exports of an unchanged object differ genuinely, cosmetically, or only in ordering | yes |
| `unifold schemadiff A B` | Diffs two dialects' schemas (9.7 vs 10.4) — the evidence the per-version mappings are written against | yes |
| `unifold explode FILE OUT/` | Export XML to a readable, diffable source tree | yes |
| `unifold implode TREE/ OUT.xml` | Rebuild an export file from an edited tree | yes |
| `unifold roundtrip FILE` | Prove an export survives explode → implode unchanged | yes |
| `unifold xref WORKSPACE/` | Trace calls across components — who calls this library proc | yes |

## The format, measured

Six real exports (one 9.7, five 10.2) are published by Rocket in
`uniface/learn-palettes`. Fetch them:

```bash
py -3 scripts/fetch_samples.py
```

They show the export is a **self-describing relational dump**, not a nested
object model:

```xml
<UNIFACE release="9.7" xmlengine="2.0">
<TABLE>
  <DSC name="USOURCE" ...>            <!-- the repository table's own schema -->
    <FLD name="UTEXT" type="B" .../>  <!-- its columns -->
  </DSC>
  <OCC>                               <!-- one row -->
    <DAT name="UTEXT" xml:space="preserve">entry OccurrenceSetFieldColors ...</DAT>
  </OCC>
</TABLE>
```

Three findings that shaped the tool:

- **ProcScript is verbatim plain text** in `DAT` elements. Not base64, not
  compressed. Extraction is straightforward.
- **9.7 and 10.2 share 100% of their element vocabulary.** The only structural
  difference is a `repversion` attribute the newer root carries. One parser
  covers both; version-specific knowledge is a table/column mapping.
- **Real exports do not parse with a stock XML parser.** They carry a UTF-8 BOM
  and reference a `UNIFACE.DTD` that is not shipped with them, using custom
  entities (`&uSEP;`, `&uFRM;`, `&uNOT;`, `&uALL;`) that a standard parser rejects as
  undefined. `unifold` handles both.

Full detail, including what is still unknown, in
[docs/FORMAT-NOTES.md](docs/FORMAT-NOTES.md).

## Why there is no schema hard-coded in this repo

Rocket does not publish the Uniface 9.7 export schema anywhere publicly
reachable — the documentation now sits behind a Rocket Community login, and the
9.7 doc tree has no `exportFileFormat` page (10.x does). Writing a parser
against a guessed schema would produce a tool that silently drops or mangles
parts of real components, which is the worst possible failure mode here.

So `unifold` discovers the schema instead of assuming it. `probe` reads a real
export and reports exactly what is in it; the exploder is then written against
a mapping derived from that report.

That decision paid for itself on first contact with real files. The measured
format bears no resemblance to what the vendor prose suggested — "objects with
aggregation relationships are nested" describes a nested object model, whereas
the files are flat repository-table dumps. A hand-written parser built from the
documentation would have been wrong in its fundamental shape. Because the format
is self-describing, the same code reads both 9.7 and 10.2 without modification.

## Usage

No dependencies beyond the standard library.

```bash
py -3 -m unittest discover -s tests
```

Probe an export:

```bash
PYTHONPATH=src py -3 -m unifold.cli probe path/to/export.xml
```

```
bom               utf-8-sig
declared encoding UTF-8
root element      UNIFACE
distinct paths    6

<UNIFACE>  n=1
  @release                  required, enum{9.7}
  <TABLE>  n=2 x2
    <DSC>  n=2
      @name                     required, enum{ULIBR, USOURCE}
      <FLD>  n=25 x22
        @name                     required, len 4-10
        @type                     required, enum{B, E, N, S}
    <OCC>  n=2
      <DAT>  n=8 x7
        @name                     required, enum{UDESCR, ULABEL, UTEXT, ...}
        #text  8/8 non-empty, len 1-1040  [text(7), multiline(1), procscript?(1)]
```

Explode an export into readable source:

```bash
PYTHONPATH=src py -3 -m unifold.cli explode samples/learn-palettes/cpt_showemployees.xml out/
```

```
out/
  manifest.txt                          what this export contained
  UFORM/SHOWEMPLOYEES/
    properties.txt                      every scalar column, sorted
    USCRIPT.proc                        the component's ProcScript
    FORMPIC.txt                         the form layout
    decoded.txt                         packed lists, made readable
  UXGROUP/EMPLOYEE/properties.txt       the component's entity
  UXFIELD/NAME/properties.txt           its fields
```

### Decoded views

Uniface packs a lot of structure into delimited strings. As stored, they are
unreadable:

```
FORMPIC: <uFRM>TYP=F<uSEP>NAM=LASTNAME<uSEP>WID=28<uSEP>HEI=1<uFRM>
WINPROP: CAPTION=<uSEP>CANRESIZE=<uSEP>MODAL=T<uSEP>SPLIT=
```

`decoded.txt` renders them. `FORMPIC` becomes a sketch of the form, with each
field padded to its declared width so the proportions survive:

```
  [EMPLOYEE.BOOTSTRAP]
   [NAME]     [LASTNAME]           [BIRTHDATE]  [AGE]   [ROLE]      [EMAIL]

  TYP  NAM                 WID  HEI  HOC  VOC
  E    EMPLOYEE.BOOTSTRAP  157  26   157  2
  F    LASTNAME            28   1    -    -
```

and packed properties become plain key/value lines:

```
  MODAL      = T
  CANRESIZE  = (empty)
```

Nested lists are handled too — Uniface embeds one list in another by prefixing
`uNOT` to the inner delimiters, and the decoder strips a level and recurses.

**These views are display-only.** `implode` reads the raw values and ignores
`decoded.txt` entirely, so decoding cannot affect fidelity — a property of the
design, not something to re-check. Edits there have no effect; change the raw
column instead.

Two decisions worth knowing about:

- **No semantic renaming.** Table and column names stay exactly as Uniface uses
  them (`UXGROUP`, not `entity`). A friendlier vocabulary would be a mapping
  claim we have not earned yet, and a wrong name is worse than an unfamiliar
  one.
- **Content decides the split, not a hardcoded list.** A column becomes its own
  file when its value is multi-line or long, and a `properties.txt` line
  otherwise. `UFORM` alone has 69 columns and the trigger set varies by table
  and version, so a curated list would rot; measuring the value does not.

`explode` refuses to write into a non-empty directory unless you pass
`--force`, and `--dry-run` lists what it would write without touching disk.

### Writing back

Edit the `.proc` files, then rebuild an importable export:

```bash
PYTHONPATH=src py -3 -m unifold.cli implode C:/temp/tree C:/temp/changed.xml
```

**`implode` does not touch your repository.** It writes an XML file, nothing
more. Importing it is a separate, deliberate act you perform in the Uniface IDE.
That boundary is the entire safety story: `unifold` never has write access to
anything of yours.

Before trusting it with your own exports, prove it can reproduce them:

```bash
PYTHONPATH=src py -3 -m unifold.cli roundtrip C:/temp/exp1.xml
```

This explodes, implodes, and compares the two documents element by element,
attribute by attribute, with `DAT` values checked byte for byte — all in a
scratch directory that is then discarded. Exit code 0 means faithful.

```
VERDICT: faithful -- every element, attribute and value survived the round trip.
```

If it ever says otherwise, do not import that export's imploded output, and
send me the differences it lists.

Byte-identical output is not the goal and is not achievable: the original's
line breaks and attribute wrapping are cosmetic and unrecorded. Semantic
identity — same elements, attributes, values and order — is the goal, because
that is what Uniface imports.

### Tracing across components

The IDE is good at navigating one component and has no answer for questions
that span components. Explode several exports into one directory, then:

```bash
PYTHONPATH=src py -3 -m unifold.cli xref C:/work/workspace --symbol OccurrenceSetFieldColors
```

```
Defined in 2 place(s)
  entry      HILIGHTROW_Include_Proc/USOURCE/HILIGHTROW/UTEXT.proc:1
  entry      cpt_showemployeeswithhighlight/UFORM/SHOWEMPLOYEES/USCRIPT.proc:20

Called from 7 place(s)
  call     bootstrap_model/UCGROUP/EMPLOYEE/UOCC_SCRIPT.proc:45
  call     cpt_showemployeeswithhighlight/UXGROUP/EMPLOYEE/UOCC_SCRIPT.proc:6
  call     prj_full_demoproject/UCGROUP/EMPLOYEE/UOCC_SCRIPT.proc:45
  ...
```

Without `--symbol` it summarises the workspace, including:

- **called but not defined here** — global library procs and services you
  haven't exported, so you know what the workspace depends on;
- **entries nothing here calls** — dead-code candidates.

Triggers and operations are deliberately excluded from the dead-code list: the
runtime fires triggers and other components activate operations, so silence in
the workspace proves nothing about them. Entries are the only fair candidates,
and even those may be called from something unexported — confirm before
deleting.

Definition and reference forms come from real exports, not invention: `entry`,
`operation` and `trigger` definitions; `call NAME(...)` and
`activate "SERVICE".OPERATION(...)` references. Comments are stripped before
indexing, respecting quoted strings, so commented-out calls don't register.

Check whether exports are stable — export the same **unchanged** object twice,
then:

```bash
PYTHONPATH=src py -3 -m unifold.cli compare first.xml second.xml --ignore-attr EXPORTED
```

It distinguishes four outcomes: identical bytes, cosmetic difference
(whitespace and attribute order), ordering-only difference, and genuine change.
Exit status is 0 when the two agree once ordering is normalised.

## What's needed next

`explode`, `implode` and `roundtrip` all work on real 9.7, 10.2 and 10.4
exports through a single code path. Every published sample round-trips
faithfully, including a 151 KB project export spanning 45 occurrences.

**Exports are byte-stable**, confirmed against a live repository: the same
component exported twice, unchanged, produced identical bytes. Nothing is
stamped at export time, so no column needs excluding and git diffs stay clean.
That was the last question blocking the design.

**10.4 is confirmed identical to 10.2** in structure — same six paths, same
repository tables, only `repversion` differs (`8` against `5`). Both `probe` and
`explode` handled a real 10.4 export with no code change.

Versions verified end to end: **9.7, 10.2, 10.4**.

Nothing external is blocking. `UNIFACE.DTD` turned out not to be on the critical
path: the entity reference is the form Uniface itself writes, so preserving the
entity name losslessly is enough to round-trip. Resolving the codepoints would
only let the real characters be *displayed*, which is cosmetic.

The one thing still untested by anyone: **whether Uniface actually imports an
imploded file cleanly.** `roundtrip` proves the document is reproduced
faithfully, which is necessary but not sufficient — the importer's own opinion
has never been asked. Try it first on a throwaway component, in a repository you
can afford to break.

Two documented routes:

1. **From the IDF**, export a single component to XML. In Uniface 9's version
   control settings there is a clustering preference for exporting *each
   component to a separate file*; turning that on is what makes the whole
   workflow viable, so it is worth checking whether it is set.
2. **Programmatically**, from ProcScript:
   `$ude("export", "component", "PCUSTOMER", "myexport.xml")`.
   `ObjectType` also accepts `entity` and `proc` (global ProcScripts).

If you can produce the **same unchanged component twice**, that is more valuable
than one file: `compare` will then tell us whether Uniface exports are
deterministic, which decides how much canonicalisation `explode` has to do.

Drop the files in `samples/` (gitignored) or `tests/fixtures/real/`.

## Layout

```
src/unifold/probe.py      schema discovery, content classification
src/unifold/compare.py    export stability measurement
src/unifold/schemadiff.py 9.7-vs-10.4 dialect comparison
src/unifold/explode.py    export XML -> readable source tree
src/unifold/implode.py    readable tree -> export XML, plus fidelity checking
src/unifold/packed.py     decoding Uniface packed lists and form layouts
src/unifold/xref.py       cross-component call graph
src/unifold/cli.py        command line
scripts/fetch_samples.py  downloads the real exports into samples/
docs/FORMAT-NOTES.md      the format as measured, plus what is still unknown
tests/test_real_exports.py regression tests against real files (skip if absent)
tests/fixtures/           SYNTHETIC fixtures - invented, not real exports
```

The fixtures in `tests/fixtures/` are clearly marked synthetic. They exercise
the code paths; they are not evidence about the real format and must not be
mistaken for it.
