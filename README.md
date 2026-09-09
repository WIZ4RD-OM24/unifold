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
**Phase 1 — blocked on a real export file.** See "What's needed next".

| Command | Does | Ready |
|---|---|---|
| `unifold probe FILE` | Reports an export's real element tree, attribute cardinalities, and where ProcScript and encoded blobs live | yes |
| `unifold compare A B` | Tells you whether two exports of an unchanged object differ genuinely, cosmetically, or only in ordering | yes |
| `unifold schemadiff A B` | Diffs two dialects' schemas (9.7 vs 10.4) — the evidence the per-version mappings are written against | yes |
| `unifold explode FILE OUT/` | Export XML to a readable source tree | not yet |

## Why there is no schema in this repo

Rocket does not publish the Uniface 9.7 export schema anywhere publicly
reachable — the documentation now sits behind a Rocket Community login, and the
9.7 doc tree has no `exportFileFormat` page (10.x does). Writing a parser
against a guessed schema would produce a tool that silently drops or mangles
parts of real components, which is the worst possible failure mode here.

So `unifold` discovers the schema instead of assuming it. `probe` reads a real
export and reports exactly what is in it; the exploder is then written against
a mapping derived from that report. See [docs/FORMAT-NOTES.md](docs/FORMAT-NOTES.md)
for what is actually established about the format, with sources, and what is
still open.

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

Check whether exports are stable — export the same **unchanged** object twice,
then:

```bash
PYTHONPATH=src py -3 -m unifold.cli compare first.xml second.xml --ignore-attr EXPORTED
```

It distinguishes four outcomes: identical bytes, cosmetic difference
(whitespace and attribute order), ordering-only difference, and genuine change.
Exit status is 0 when the two agree once ordering is normalised.

## What's needed next

Phase 1 needs **real export files — ideally one from 9.7 and one from 10.4**.
Nothing else is blocking. Any component will do; the smaller and less sensitive
the better, and a demo or scratch component is ideal. The same component
exported from both versions is the single most useful thing, because
`schemadiff` can then map the two dialects onto each other directly.

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
src/unifold/cli.py        command line
docs/FORMAT-NOTES.md     what is established about the format, with sources
tests/fixtures/          SYNTHETIC fixtures - invented, not real exports
```

The fixtures in `tests/fixtures/` are clearly marked synthetic. They exercise
the code paths; they are not evidence about the real format and must not be
mistaken for it.
