# unifold

Read Uniface repository exports as human-readable, diffable source.

Uniface keeps component definitions, ProcScript and entity models as rows in a
DBMS repository, not as files. That single fact is why no modern editor, code
review tool, or AI coding assistant can be pointed at a Uniface codebase: there
is nothing on disk for them to read. The supported way out — XML export — gives
you text, but text shaped like a serialised repository transaction, not like
source code. A three-line ProcScript change produces a diff nobody can review.

`unifold` closes that gap: export XML in, a clean directory tree of `.proc` and
`.yaml` files out, stable enough that git diffs are readable and Claude Code can
work on the result.

Target: **Uniface 9.7 and 10.4**. Read-only — `unifold` never writes to your
repository.

Both versions export XML rooted at `<UNIFACE>`, but Uniface 10 restructured the
repository, so the element vocabulary and nesting differ. That is a mapping
problem, not two tools:

```
9.7 export ─┐
            ├─► dialect detector ─► per-version mapping ─► neutral model ─► same output tree
10.4 export ┘
```

`probe`, `compare` and `schemadiff` are already version-agnostic — they assume
nothing beyond well-formed XML. Only the mapping layer is version-specific.

Because both dialects explode to the same tree, a component can be diffed
across a 9.7 → 10.x migration, which is otherwise painful to verify.

## Status

**Phase 0 — shipped.** Format discovery and stability measurement.
**Phase 1 — shipped.** `explode` turns real exports into readable source.
**Phase 2 — next.** `implode` (write back), and semantic naming.

| Command | Does | Ready |
|---|---|---|
| `unifold probe FILE` | Reports an export's real element tree, attribute cardinalities, and where ProcScript and encoded blobs live | yes |
| `unifold compare A B` | Tells you whether two exports of an unchanged object differ genuinely, cosmetically, or only in ordering | yes |
| `unifold schemadiff A B` | Diffs two dialects' schemas (9.7 vs 10.4) — the evidence the per-version mappings are written against | yes |
| `unifold explode FILE OUT/` | Export XML to a readable, diffable source tree | yes |

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
  entities (`&uSEP;`, `&uFRM;`, `&uALL;`) that a standard parser rejects as
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
root element      UNIFACE
distinct paths    7

<UNIFACE>  n=1
  <UFORM>  n=1
    @NAME                     required, len 9-9
    <UPROC>  n=2 x2
      @TRIGGER                  required, enum{EXEC, QUIT}
      #text  2/2 non-empty, len 102-208  [multiline(2), procscript?(2)]
    <ULAYOUT>  n=1
      #text  1/1 non-empty, len 152-152  [base64(1)]
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
  UXGROUP/EMPLOYEE/properties.txt       the component's entity
  UXFIELD/NAME/properties.txt           its fields
```

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

Check whether exports are stable — export the same **unchanged** object twice,
then:

```bash
PYTHONPATH=src py -3 -m unifold.cli compare first.xml second.xml --ignore-attr EXPORTED
```

It distinguishes four outcomes: identical bytes, cosmetic difference
(whitespace and attribute order), ordering-only difference, and genuine change.
Exit status is 0 when the two agree once ordering is normalised.

## What's needed next

`explode` works on real 9.7 and 10.2 exports today, through a single code path.

Still unanswerable from published samples: **is re-exporting an unchanged object
byte-stable?** That needs the same component exported twice from a live
repository. `explode` already sorts tables, occurrences and columns, so ordering
churn inside the XML cannot reach the output — but if Uniface rewrites
timestamps or version counters on every export, `properties.txt` will churn and
those columns will need excluding. `compare` is built and waiting for the two
files that settle it.

Also worth having: a genuine **10.4** export, to confirm nothing moved between
the 10.2 measured here and 10.4; and the real codepoints behind `&uSEP;` /
`&uFRM;` / `&uALL;`, which `implode` will need in order to round-trip safely.

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
src/unifold/cli.py        command line
scripts/fetch_samples.py  downloads the real exports into samples/
docs/FORMAT-NOTES.md      the format as measured, plus what is still unknown
tests/test_real_exports.py regression tests against real files (skip if absent)
tests/fixtures/           SYNTHETIC fixtures - invented, not real exports
```

The fixtures in `tests/fixtures/` are clearly marked synthetic. They exercise
the code paths; they are not evidence about the real format and must not be
mistaken for it.
