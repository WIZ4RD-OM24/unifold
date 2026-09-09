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

## Open questions — must be answered from real export files

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
