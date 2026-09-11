"""Tests for the MCP server.

Two things matter here. The protocol has to be right, or no client can talk to
the server at all. And the server has to stay read-only -- an assistant should
be able to explore a Uniface codebase with no possibility of changing it.
"""

import io
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from unifold import explode as explode_mod  # noqa: E402
from unifold import mcp  # noqa: E402

SAMPLES = ROOT / "samples" / "learn-palettes"
SKIP = not SAMPLES.is_dir()
REASON = "real samples absent -- run scripts/fetch_samples.py"


def converse(*messages):
    """Feed messages through the stdio loop and return parsed responses."""
    stdin = io.StringIO("".join(json.dumps(m) + "\n" for m in messages))
    stdout = io.StringIO()
    mcp.serve(stdin, stdout)
    return [json.loads(line) for line in stdout.getvalue().splitlines() if line]


def request(message_id, method, **params):
    message = {"jsonrpc": "2.0", "id": message_id, "method": method}
    if params:
        message["params"] = params
    return message


class TestProtocol(unittest.TestCase):
    def test_initialize_reports_protocol_and_server(self):
        (response,) = converse(request(1, "initialize"))
        self.assertEqual(response["jsonrpc"], "2.0")
        self.assertEqual(response["id"], 1)
        result = response["result"]
        self.assertEqual(result["protocolVersion"], mcp.PROTOCOL_VERSION)
        self.assertIn("tools", result["capabilities"])
        self.assertEqual(result["serverInfo"]["name"], "unifold")

    def test_notifications_are_never_answered(self):
        # A response to a notification is a protocol violation.
        responses = converse(
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            request(1, "initialize"),
        )
        self.assertEqual(len(responses), 1)
        self.assertEqual(responses[0]["id"], 1)

    def test_tools_list_is_well_formed(self):
        (response,) = converse(request(1, "tools/list"))
        tools = response["result"]["tools"]
        self.assertTrue(tools)
        for tool in tools:
            with self.subTest(tool["name"]):
                self.assertTrue(tool["description"])
                schema = tool["inputSchema"]
                self.assertEqual(schema["type"], "object")
                for name in schema["required"]:
                    self.assertIn(name, schema["properties"])

    def test_unknown_method_returns_a_jsonrpc_error(self):
        (response,) = converse(request(1, "no/such/method"))
        self.assertEqual(response["error"]["code"], -32601)

    def test_malformed_json_does_not_kill_the_server(self):
        stdin = io.StringIO('{ not json\n' + json.dumps(request(1, "initialize")) + "\n")
        stdout = io.StringIO()
        mcp.serve(stdin, stdout)
        responses = [json.loads(l) for l in stdout.getvalue().splitlines() if l]
        self.assertEqual(responses[0]["error"]["code"], -32700)
        self.assertEqual(responses[1]["id"], 1)

    def test_blank_lines_are_ignored(self):
        stdin = io.StringIO("\n\n" + json.dumps(request(7, "initialize")) + "\n")
        stdout = io.StringIO()
        mcp.serve(stdin, stdout)
        self.assertEqual(len(stdout.getvalue().splitlines()), 1)


class TestToolErrors(unittest.TestCase):
    def call(self, tool, **arguments):
        (response,) = converse(
            request(1, "tools/call", name=tool, arguments=arguments)
        )
        return response["result"]

    def test_missing_workspace_is_reported_not_raised(self):
        result = self.call("find_symbol", name="X")
        self.assertTrue(result["isError"])
        self.assertIn("workspace", result["content"][0]["text"])

    def test_missing_name_is_reported(self):
        result = self.call("find_symbol", workspace=str(ROOT))
        self.assertTrue(result["isError"])
        self.assertIn("name", result["content"][0]["text"])

    def test_nonexistent_workspace_is_reported(self):
        result = self.call("workspace_summary", workspace=str(ROOT / "nope"))
        self.assertTrue(result["isError"])
        self.assertIn("Not a directory", result["content"][0]["text"])

    def test_unknown_tool_is_reported(self):
        result = self.call("delete_everything", workspace=str(ROOT))
        self.assertTrue(result["isError"])
        self.assertIn("Unknown tool", result["content"][0]["text"])


class TestReadOnly(unittest.TestCase):
    def test_no_tool_advertises_a_write_capability(self):
        (response,) = converse(request(1, "tools/list"))
        names = [t["name"] for t in response["result"]["tools"]]
        for forbidden in ("explode", "implode", "write", "delete", "import"):
            for name in names:
                self.assertNotIn(forbidden, name,
                                 "%s looks like it mutates something" % name)

    def test_calling_every_tool_leaves_the_workspace_untouched(self):
        tmp = Path(tempfile.mkdtemp(prefix="unifold-mcp-"))
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        directory = tmp / "e" / "UFORM" / "COMPA"
        directory.mkdir(parents=True)
        explode_mod.write_text(directory / "properties.txt", "ULABEL: COMPA\n")
        explode_mod.write_text(directory / "USCRIPT.proc", "entry a\nend\n")

        def snapshot():
            return {p.relative_to(tmp).as_posix(): p.read_bytes()
                    for p in sorted(tmp.rglob("*")) if p.is_file()}

        before = snapshot()
        (listing,) = converse(request(1, "tools/list"))
        for tool in listing["result"]["tools"]:
            converse(request(2, "tools/call", name=tool["name"],
                             arguments={"workspace": str(tmp), "name": "COMPA",
                                        "file": str(tmp / "missing.xml")}))
        self.assertEqual(snapshot(), before,
                         "an MCP tool modified the workspace")


@unittest.skipIf(SKIP, REASON)
class TestAgainstRealExports(unittest.TestCase):
    def setUp(self):
        from unifold import explode as explode_mod

        self.tmp = Path(tempfile.mkdtemp(prefix="unifold-mcp-real-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        for source in sorted(SAMPLES.glob("*.xml")):
            explode_mod.explode(source, self.tmp / source.stem, force=True)

    def call(self, tool, **arguments):
        (response,) = converse(
            request(1, "tools/call", name=tool, arguments=arguments)
        )
        self.assertFalse(response["result"]["isError"],
                         response["result"]["content"][0]["text"])
        return response["result"]["content"][0]["text"]

    def test_find_symbol_traces_a_library_proc(self):
        text = self.call("find_symbol", workspace=str(self.tmp),
                         name="OccurrenceSetFieldColors")
        self.assertIn("Defined in", text)
        self.assertIn("Called from", text)

    def test_find_entity_reports_the_blast_radius(self):
        text = self.call("find_entity", workspace=str(self.tmp), name="EMPLOYEE")
        self.assertIn("SHOWEMPLOYEES", text)

    def test_workspace_summary_describes_the_codebase(self):
        text = self.call("workspace_summary", workspace=str(self.tmp))
        self.assertIn("Workspace index", text)

    def test_probe_export_reads_a_file_inside_the_workspace(self):
        shutil.copy(SAMPLES / "cpt_showemployees.xml", self.tmp / "export.xml")
        text = self.call("probe_export", workspace=str(self.tmp),
                         file="export.xml")
        self.assertIn("UNIFACE", text)


class TestPathConfinement(unittest.TestCase):
    """The MCP surface must not become an arbitrary-file reader.

    `probe` prints the opening bytes of anything that is not XML -- a fair
    diagnostic for someone running the CLI on their own files, and a file-read
    primitive if a client can name any path on the machine.
    """

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="unifold-confine-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.secret = self.tmp / "secret.txt"
        self.secret.write_text("SECRET-CANARY", encoding="utf-8")
        self.workspace = self.tmp / "ws"
        self.workspace.mkdir()

    def probe(self, path):
        (response,) = converse(request(
            1, "tools/call",
            name="probe_export",
            arguments={"workspace": str(self.workspace), "file": str(path)},
        ))
        return response["result"]

    def test_absolute_path_outside_the_workspace_is_refused(self):
        result = self.probe(self.secret)
        self.assertTrue(result["isError"])
        self.assertNotIn("SECRET-CANARY", result["content"][0]["text"])

    def test_relative_traversal_is_refused(self):
        result = self.probe("../secret.txt")
        self.assertTrue(result["isError"])
        self.assertNotIn("SECRET-CANARY", result["content"][0]["text"])

    def test_a_file_inside_the_workspace_is_allowed(self):
        inside = self.workspace / "inside.txt"
        inside.write_text("harmless", encoding="utf-8")
        self.assertFalse(self.probe("inside.txt")["isError"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
