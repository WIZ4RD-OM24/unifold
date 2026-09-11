"""An MCP server exposing a workspace to an AI assistant.

Running `unifold xref --symbol X` and pasting the output into a chat works, but
it makes the assistant dependent on you to look things up. This serves the same
queries over the Model Context Protocol so it can ask directly: who calls this
library proc, which components use this entity, what does this component touch.

Deliberately **read-only**. Every tool here answers a question; none writes a
file, rebuilds an export, or touches a repository. An assistant should be able
to explore a Uniface codebase freely without any possibility of changing it, and
the way to guarantee that is not to offer the capability.

The protocol is implemented directly rather than via an SDK so the package keeps
its promise of no third-party dependencies. Transport is newline-delimited
JSON-RPC 2.0 over stdin/stdout, which is what MCP's stdio transport specifies.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from . import probe as probe_mod
from .implode import contained
from . import usage as usage_mod
from . import workspace as workspace_mod
from . import xref as xref_mod

PROTOCOL_VERSION = "2024-11-05"
SERVER_NAME = "unifold"

WORKSPACE_ARG = {
    "type": "string",
    "description": "Path to a directory containing exploded Uniface trees.",
}


def tool_definitions() -> list:
    def spec(name, description, extra=None, required=("workspace",)):
        properties = {"workspace": dict(WORKSPACE_ARG)}
        properties.update(extra or {})
        return {
            "name": name,
            "description": description,
            "inputSchema": {
                "type": "object",
                "properties": properties,
                "required": list(required),
            },
        }

    return [
        spec(
            "workspace_summary",
            "Overview of a Uniface workspace: components, library ProcScript, "
            "the data model in use, and external dependencies. Start here when "
            "you do not yet know what the codebase contains.",
        ),
        spec(
            "find_symbol",
            "Find where a ProcScript name (an entry, operation or trigger) is "
            "defined and everywhere it is called from, across all components "
            "in the workspace. Use this to trace a library proc.",
            {"name": {"type": "string",
                      "description": "The entry, operation or trigger name."}},
            required=("workspace", "name"),
        ),
        spec(
            "find_entity",
            "List every component that uses a given entity, and where it is "
            "defined in the application model. Use this for impact analysis "
            "before changing an entity.",
            {"name": {"type": "string", "description": "Entity name, bare or "
                                                       "qualified as MODEL.ENTITY."}},
            required=("workspace", "name"),
        ),
        spec(
            "find_field",
            "List every component that uses a given field, with the field's "
            "declared data type. Use this for impact analysis before changing "
            "a field.",
            {"name": {"type": "string",
                      "description": "Field name, bare or qualified as "
                                     "MODEL.ENTITY.FIELD."}},
            required=("workspace", "name"),
        ),
        spec(
            "describe_component",
            "What one component uses and calls: its entities and fields, what "
            "ProcScript it defines, and what it calls out to.",
            {"name": {"type": "string", "description": "Component name."}},
            required=("workspace", "name"),
        ),
        spec(
            "unresolved_calls",
            "Names called in the workspace but defined nowhere in it, and "
            "entries nothing calls. Shows what the code depends on but has not "
            "exported, and dead-code candidates.",
        ),
        spec(
            "probe_export",
            "Report the structure of a raw Uniface export file: its element "
            "tree, repository tables, columns and where ProcScript lives. Use "
            "when an export will not explode or looks wrong. The file must sit "
            "inside the given workspace.",
            {"file": {"type": "string",
                      "description": "Path to a Uniface export XML file, "
                                     "inside the workspace."}},
            required=("workspace", "file"),
        ),
    ]


def require_workspace(arguments: dict) -> Path:
    raw = (arguments or {}).get("workspace")
    if not raw:
        raise ValueError("A 'workspace' path is required.")
    root = Path(raw)
    if not root.is_dir():
        raise ValueError("Not a directory: %s" % root)
    return root


def require_name(arguments: dict) -> str:
    name = (arguments or {}).get("name")
    if not name:
        raise ValueError("A 'name' is required.")
    return name


def call_tool(name: str, arguments: dict) -> str:
    arguments = arguments or {}

    root = require_workspace(arguments)

    if name == "probe_export":
        raw = arguments.get("file")
        if not raw:
            raise ValueError("A 'file' path is required.")
        # Confined to the workspace on purpose. probe reports the opening bytes
        # of anything that is not XML, which is a useful diagnostic for someone
        # running the CLI on their own files and an arbitrary-file read if an
        # assistant can name any path. The MCP surface stays narrow.
        path = contained(root, raw, source="The requested file")
        if not path.is_file():
            raise ValueError("No such file in the workspace: %s" % raw)
        return probe_mod.render(probe_mod.probe(path), show_samples=False)

    if name == "workspace_summary":
        return workspace_mod.render(root)
    if name == "find_symbol":
        return xref_mod.render_symbol(xref_mod.build(root), require_name(arguments))
    if name == "find_entity":
        return usage_mod.render_lookup(
            usage_mod.build(root), require_name(arguments), "entity")
    if name == "find_field":
        return usage_mod.render_lookup(
            usage_mod.build(root), require_name(arguments), "field")
    if name == "describe_component":
        component = require_name(arguments)
        return (usage_mod.render_component(usage_mod.build(root), component)
                + xref_mod.render_component(xref_mod.build(root), component))
    if name == "unresolved_calls":
        return xref_mod.render(xref_mod.build(root))

    raise ValueError("Unknown tool: %s" % name)


def handle(message: dict):
    """Return a response dict, or None for notifications."""
    method = message.get("method")
    message_id = message.get("id")

    # Notifications carry no id and must never be answered.
    if message_id is None:
        return None

    if method == "initialize":
        return result(message_id, {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {"tools": {}},
            "serverInfo": {"name": SERVER_NAME, "version": version()},
        })

    if method == "tools/list":
        return result(message_id, {"tools": tool_definitions()})

    if method == "tools/call":
        params = message.get("params") or {}
        try:
            text = call_tool(params.get("name"), params.get("arguments"))
        except Exception as exc:                    # reported, not crashed
            return result(message_id, {
                "content": [{"type": "text", "text": str(exc)}],
                "isError": True,
            })
        return result(message_id, {
            "content": [{"type": "text", "text": text}],
            "isError": False,
        })

    if method in ("ping", "shutdown"):
        return result(message_id, {})

    return error(message_id, -32601, "Method not found: %s" % method)


def version() -> str:
    from . import __version__
    return __version__


def result(message_id, payload) -> dict:
    return {"jsonrpc": "2.0", "id": message_id, "result": payload}


def error(message_id, code, text) -> dict:
    return {"jsonrpc": "2.0", "id": message_id,
            "error": {"code": code, "message": text}}


def serve(stdin=None, stdout=None) -> int:
    stdin = stdin or sys.stdin
    stdout = stdout or sys.stdout
    for line in stdin:
        line = line.strip()
        if not line:
            continue
        try:
            message = json.loads(line)
        except json.JSONDecodeError as exc:
            write(stdout, error(None, -32700, "Parse error: %s" % exc))
            continue
        response = handle(message)
        if response is not None:
            write(stdout, response)
    return 0


def write(stream, payload) -> None:
    stream.write(json.dumps(payload) + "\n")
    stream.flush()
