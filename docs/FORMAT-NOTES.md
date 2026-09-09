# Uniface export format — what is actually established

Everything here is sourced. Anything we have *not* verified is listed under
"Open questions" rather than assumed. The parser is written against verified
facts plus a schema discovered from real files (`unifold probe`) — it does not
hard-code a guessed schema.

## Established

**Uniface is repository-based, not file-based.** Component definitions,
ProcScript and entity models live as rows in a DBMS repository, not as files.
This is the root cause of the tooling gap: every modern editor and AI coding
tool assumes a directory of text files.

**Export is the only supported way out.**
- Interactive: IDF export in the development environment.
- Programmatic: `$ude("export", ObjectType, ObjectProfile, FileName, {OptionList})`.
  `ObjectType` covers `component`, `entity`, `proc` (global ProcScripts),
  projects and libraries. `FileName` accepts an XML path or a zip target of the
  form `xml:archive.zip:filename.xml`. Returns a count of attempted records;
  detail comes from `$procReturnContext`.

**Format by version.**
- TRX is the legacy proprietary ASCII interchange format. Rocket's own glossary
  calls it obsolete and states it has not been used since Uniface 8; it is not
  supported as a Data Copy Facility target, and export requires selecting XML
  rather than TRX. Uniface has since moved to Unicode.
- Uniface 9 and 10 export development-object definitions as XML.
- Legacy repository-table copies (`IDF.EXE ... /cpy IDF:UCTABLE.DICT TRX:...`)
  are still seen in the field against tables `UCTABLE`, `UCGROUP`, `UCKEY`,
  `UCFIELD`, `UCRELSH`. These are raw repository rows, not object definitions —
  a different and lower-level thing than an object export.

**XML shape (as far as Rocket documents it publicly).**
- Root element `UNIFACE`.
- "Well-formed, well-defined XML in which objects that have aggregation
  relationships are nested."
- The export facility honours referential-integrity constraints, because a
  single development object's definition is spread across several repository
  entities and all of them must move together.

**Version-control integration exists but is coarse.**
- Uniface 9 has a version-control interface; clustering preferences can be set
  so that *each component exports to a separate file*. This is the setting our
  workflow should depend on.
- Uniface 10 integrates with Git/GitHub; UD6 integration arrived in 10.2.2.
- `github.com/uniface/WASListener` (C++) is required to import WorkArea export
  files back into the repository database. Last updated Jul 2026 — the one
  actively maintained repo in that org.
- march-hare's UD6/CMtool driver stores Uniface source as plain ASCII XML in a
  directory tree defined by the assignment and joins files, one file per
  component.

## MEASURED: the actual export format

Established by probing six real exports published by Rocket in
`uniface/learn-palettes` — one from 9.7 and five from 10.2, spanning an include
proc, an application model, three components and a project. Fetch them with
`py -3 scripts/fetch_samples.py`. This section supersedes guesswork; where it
contradicts the vendor prose below, believe this section.

**The export is a self-describing relational dump, not a nested object model.**
This is the central finding and it reshapes the exploder's design.

```xml
<?xml version='1.0' encoding='UTF-8' ?>          <!-- preceded by a UTF-8 BOM -->
<!-- Created by Uniface - (C) Uniface B.V. All rights reserved -->
<!DOCTYPE UNIFACE PUBLIC "UNIFACE.DTD" "UNIFACE.DTD">
<UNIFACE release="9.7" xmlengine="2.0">
<TABLE>
  <DSC name="USOURCE" model="DICT" system="S" pseudo="73" level="1" ...>
    <FLD name="UTEXT" seqno="8" type="B" level="2" length="0" .../>
  </DSC>
  <OCC>
    <DAT name="UTEXT" xml:space="preserve">entry OccurrenceSetFieldColors ...</DAT>
  </OCC>
</TABLE>
</UNIFACE>
```

- `TABLE` — one repository table. A file holds as many as the object needs.
- `DSC` — that table's schema, carrying its own column definitions as `FLD`
  elements. The export describes itself; no external schema is required.
- `OCC` — one occurrence, i.e. one row.
- `DAT` — one column value, named by `@name`, matching a `FLD` in the `DSC`.

Exactly six element paths appear, in every file, at both versions and for every
object type: `UNIFACE`, `.../TABLE`, `.../DSC`, `.../DSC/FLD`, `.../OCC`,
`.../OCC/DAT`. Repository tables observed so far: `ULIBR`, `USOURCE`, `UFORM`.

**ProcScript is stored verbatim as plain text** in `DAT` elements — with
`xml:space="preserve"`, tabs and all. It is not base64, not compressed, not
escaped beyond normal XML rules. Extraction is therefore straightforward, which
is the best possible news for the exploder.

**Two traps that a guessed parser would have hit:**

1. Every export begins with a **UTF-8 BOM**, before the XML declaration.
2. The `DOCTYPE` references `UNIFACE.DTD`, which is **not shipped with the
   export**, and the files use custom entities defined in it — `&uSEP;`,
   `&uFRM;`, `&uALL;`, evidently Uniface separator characters. A standard parser
   fails outright with "undefined entity". `unifold` rewrites the DOCTYPE with
   an internal subset declaring each entity as a traceable `[[name]]`
   placeholder, so files parse and nothing is silently dropped. **Their real
   character values remain unknown** and must be resolved before `implode` can
   round-trip safely.

**Version compatibility is far better than expected.** `schemadiff` on the real
9.7 file against a real 10.2 file reports 100% element-vocabulary overlap, no
paths unique to either side, and exactly one attribute difference: 10.2 adds
`repversion` on the root. The container format is shared; what differs between
versions is which repository tables and columns appear inside it — and the `DSC`
blocks describe those at runtime. One parser covers both; the version-specific
knowledge is a table/column mapping, not a separate front end.

**What the custom entities are for** (inferred from exploded content, so the
role is evidenced even though the codepoints are not). `&uSEP;` separates
key=value pairs inside packed property strings, and `&uFRM;` delimits widget
descriptors in the layout column:

```
WINPROP: CAPTION=<uSEP>CANRESIZE=<uSEP>MODAL=T<uSEP>SPLIT=
FORMPIC: <uFRM>TYP=F<uSEP>NAM=LASTNAME<uSEP>WID=28<uSEP>HEI=1<uFRM>
```

So `FORMPIC` is the form layout, and it is structured text rather than an opaque
blob — parseable into something readable once the separators are pinned down.

### Still open after measurement

- The actual codepoints of `&uSEP;`, `&uFRM;` and `&uALL;` (roles above are
  evidenced; the byte values are not, and `implode` will need them).
- The meaning of the packed `varinfo` attribute on `FLD` (observed values embed
  escapes such as `\1D`, `\1E`, `\1F`).
- `FLD@type` codes: `B`, `E`, `N`, `S` observed; `B` carries ProcScript.
- Whether 10.4 differs from the 10.2 measured here.

**Answered: exports are byte-stable.** A component exported twice from a live
repository, unchanged in between, produced byte-identical files (`compare`
verdict: "identical bytes -- export is fully deterministic"). Uniface stamps
nothing at export time — no export timestamp, no regenerated counters, no
reordering of tables, rows or columns.

Two consequences:

- No column needs excluding from `properties.txt`. `UTIMESTAMP`, `UKVERSION`
  and `UMVERSION` appear in the output, but they are stored properties of the
  object rather than export-time artefacts, so they only change when the object
  does.
- `explode`'s sorting of tables, occurrences and columns is defensive rather
  than load-bearing. It stays, because determinism should not depend on the
  vendor continuing to be well behaved, but it is not doing work today.

This is what makes the whole idea viable: a three-line ProcScript change
produces a three-line diff.

Caveat on scope: one component, one repository, one run. It is strong evidence,
not a guarantee across every object type and version.

## Supporting 9.7 and 10.4 together

Both versions export XML rooted at `UNIFACE`, so the file-level contract is
shared. What differs is the vocabulary: Uniface 10 restructured the repository
and the IDE, and 10.x documentation carries a dedicated `exportFileFormat` page
that the 9.7 tree does not. We therefore expect different element names and
nesting for the same underlying concepts (component, entity, field, trigger,
ProcScript, layout).

The design consequence is that only the mapping layer is version-specific. The
neutral model must be the union of both repository models, or it silently loses
whatever one version expresses and the other does not — which is why it cannot
be designed before both dialects have been measured.

Note also that 10.x users already have partial alternatives (Git integration,
UD6 from 10.2.2), whereas 9.7 users have essentially nothing. The 9.7 gap is
the more acute one even though the tool targets both.

## Original open questions (kept for the record)

All six are now answered by the measured section above: the schema is known (1),
ProcScript is verbatim text in `DAT` (2), nothing is base64 or compressed (3),
encoding is UTF-8 with a BOM (4), exports are byte-stable (5), and one file
holds many tables (6).



These are exactly what `unifold probe` is built to answer:

1. Element and attribute names under `<UNIFACE>` for a 9.7 component export.
   (The `/1000/` doc tree has a dedicated `exportFileFormat` page; the `/0907/`
   tree does not appear to, so 9.7's shape must be read off a real file.)
2. Where ProcScript lives: element text, CDATA, or an attribute — and whether
   it is stored verbatim, entity-escaped, or encoded.
3. Whether any field is base64/compressed (layout blobs are the likely
   candidate).
4. Declared encoding and actual byte encoding (9.7 is Unicode-era, but the
   declaration should be confirmed, not assumed).
5. Whether element/attribute ordering is stable across two exports of an
   unchanged object. This determines whether we need canonical re-ordering to
   get clean diffs — and it is the single most important question for the
   product, because unstable ordering is *why* raw export XML is useless in
   version control.
6. Whether one file holds one object or many, under the 9.7 "separate file per
   component" clustering preference.
7. How far the 9.7 and 10.4 vocabularies actually diverge, and whether every
   9.7 concept has a 10.4 counterpart (and vice versa). `unifold schemadiff`
   answers this directly once both files exist. Anything expressible in one
   dialect but not the other constrains the neutral model.

## Sources

- Rocket Uniface docs, Export and Import Facilities; Data Copy Facility; TRX
  format glossary entry (`documentation.uniface.com` / `www3.rocketsoftware.com`;
  now largely behind a Rocket Community login).
- `dev.to/petercode` — practical guide to `$ude("export")`.
- Uniface-L thread "Exporting an Entity" (narkive) — CASE Unload/Load, the
  `IDF.EXE /cpy` recipe, repository table names.
- march-hare.com — "Configuration Management applied to Uniface", UD6/CMtool.
- Rocket Forum — "Uniface 9: Version Control with Uniface 9 and GIT".
- github.com/uniface — org repo listing.
