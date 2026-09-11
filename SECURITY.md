# Security

## Reporting a vulnerability

Open an issue at <https://github.com/WIZ4RD-OM24/unifold/issues>. If the report
would itself disclose something sensitive, say so in the issue without the
detail and ask for a private channel first.

## What the tool does and does not do

`unifold` is a local command-line tool. It reads and writes files, nothing else.

- **No network access at runtime.** Nothing in `src/unifold/` opens a socket or
  makes a request. The only code that reaches the network is
  `scripts/fetch_samples.py`, a development helper that downloads Rocket's
  public sample exports, and it is never invoked by the CLI. A test asserts that
  the runtime modules import no networking or process-spawning libraries.
- **No telemetry.** Nothing is collected, counted or phoned home.
- **No subprocesses, no `eval`.** Also covered by that test.
- **No connection to a Uniface repository, ever.** `implode` writes an XML file;
  importing it is a separate act you perform in the IDE. There is no code path
  by which `unifold` can modify a repository.
- **No third-party dependencies.** The entire supply chain is Python's standard
  library, which removes a whole class of risk from a tool that will be pointed
  at proprietary source.

## Trust boundaries

Two kinds of input arrive from elsewhere and are treated as untrusted:

**Export files** come from a colleague, a shared drive, a vendor, a repository.
**Exploded trees** are designed to be shared — committed to git, sent for
review, copied between machines — so `_unifold.json` is untrusted input even
though `explode` normally writes it.

## Protections

### XML entity attacks

Uniface exports declare a `DOCTYPE` referencing a `UNIFACE.DTD` that is not
shipped with them, and use custom entities from it. `prepare()` replaces the
document's `DOCTYPE` — including any internal subset it carries — with a flat
one declaring each referenced entity as an inert `[[name]]` placeholder.

This neutralises both classic attacks, as a consequence of the design rather
than as a bolt-on:

- **External entity (XXE):** an attacker's `<!ENTITY x SYSTEM "file:///...">` is
  discarded along with the rest of the subset, so no local file or URL is ever
  resolved.
- **Entity expansion (billion laughs):** nesting is stripped, since every
  replacement is a literal string that cannot reference another entity.

Both are covered by regression tests in `tests/test_security.py` — the point
being that a future change to `prepare()` cannot quietly remove the protection.

### Path traversal

`explode` derives directory names from table and column names inside the export.
Every path segment goes through `safe_name()`, which reduces anything outside
`[A-Za-z0-9_.-]` to an underscore, resolves `..` to a safe placeholder, and
escapes Windows reserved device names. A test asserts every written path
resolves inside the output directory.

`implode` reads paths from `_unifold.json`. Each is resolved and checked to be
inside the tree; anything that escapes raises `UnsafePath`, nothing is written,
and the CLI exits `3`. Without this, a hand-edited sidecar could point at
`../../../.ssh/id_rsa` and have `implode` read it into the export you then
import or commit.

### MCP server

`unifold mcp` is **read-only by construction**. Every tool answers a question;
none writes a file, rebuilds an export or touches a repository. Two tests
enforce this: one snapshots a workspace, calls every advertised tool and asserts
nothing changed; another rejects any tool whose name suggests mutation.

`probe_export` is confined to the given workspace. `probe` reports the opening
bytes of a file that is not XML — a fair diagnostic for someone running the CLI
on their own files, and an arbitrary-file read if a client could name any path.
The CLI keeps the diagnostic; the MCP surface does not.

Note that the server runs with your privileges and reads whatever workspace the
client names. Point it at directories you are willing to have read.

## Known limitations

- **Memory.** Exports are parsed in full rather than streamed, so a pathological
  file could exhaust memory. Real exports are small; a 151 KB project export is
  the largest tested.
- **Workspace scanning.** `xref` and `index` walk the directory they are given.
  Pointing them at a very large tree will be slow.
- **The importer is untested.** No one has yet confirmed Uniface accepts an
  imploded file. Use a throwaway component in a repository you can afford to
  break before relying on the write-back path.

## Handling sensitive source

Exploded trees contain your components verbatim. `unifold` cannot know which of
that is confidential, so a few habits are worth having:

- `.gitignore` already excludes `samples/`, `readable/`, `out/`, `tree/` and
  stray `*.xml`. Check it still matches your layout before your first commit.
- Deleting a file from a git repository does not remove it from history. If
  proprietary source is committed by mistake, the history needs rewriting and
  the remote's copies purging — and the content should be treated as disclosed.
- Prefer a private repository for exploded trees of production code.
