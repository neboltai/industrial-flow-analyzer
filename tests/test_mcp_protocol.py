"""MCP protocol behaviour, manifest consistency and SDK interoperability.

The dual-era rules implemented by ``mcp/server.py`` come from revision
2026-07-28 of the specification:

* modern requests carry their version in ``params._meta``;
* ``server/discover`` is mandatory and returns the supported modern versions;
* an unsupported version is refused with ``-32022`` and a ``supported`` list;
* legacy clients keep the ``initialize`` handshake.

The last test in this module drives the server with the *official* Python SDK
rather than with expectations written alongside the server, so a protocol
mistake cannot pass by agreeing with itself.
"""

from __future__ import annotations

import json
import subprocess
import sys
import unittest
from pathlib import Path
from typing import Any, Dict, List

from fixtures import ROOT

SERVER = ROOT / "mcp" / "server.py"
MCP_CONFIG = ROOT / ".mcp.json"
CODEX_MANIFEST = ROOT / ".codex-plugin" / "plugin.json"
CLAUDE_MANIFEST = ROOT / ".claude-plugin" / "plugin.json"

META_VERSION = "io.modelcontextprotocol/protocolVersion"
MODERN_VERSION = "2026-07-28"
LEGACY_VERSIONS = ("2025-11-25", "2025-06-18", "2025-03-26", "2024-11-05")
DATASET_ID = "fictional-small-warehouse"

try:  # pragma: no cover - import guard, exercised by whichever branch applies
    import mcp as mcp_sdk  # noqa: F401
    from mcp_types.version import (
        HANDSHAKE_PROTOCOL_VERSIONS,
        MODERN_PROTOCOL_VERSIONS,
    )

    SDK_AVAILABLE = True
except ImportError:  # pragma: no cover
    SDK_AVAILABLE = False


def session(requests: List[Dict[str, Any]]) -> Dict[Any, Dict[str, Any]]:
    payload = "\n".join(json.dumps(request) for request in requests) + "\n"
    process = subprocess.run(
        [sys.executable, str(SERVER)],
        input=payload,
        capture_output=True,
        text=True,
        timeout=180,
    )
    assert process.returncode == 0, process.stderr
    responses = [json.loads(line) for line in process.stdout.splitlines() if line.strip()]
    return {r.get("id"): r for r in responses}


def modern(request_id: int, method: str, params: Dict[str, Any] | None = None) -> Dict[str, Any]:
    body = dict(params or {})
    body["_meta"] = {META_VERSION: MODERN_VERSION, "io.modelcontextprotocol/clientCapabilities": {}}
    return {"jsonrpc": "2.0", "id": request_id, "method": method, "params": body}


class ModernEraTest(unittest.TestCase):
    def test_server_discover_is_implemented(self) -> None:
        result = session([modern(1, "server/discover")])[1]["result"]
        self.assertEqual(result["supportedVersions"], [MODERN_VERSION])
        self.assertIn("tools", result["capabilities"])
        self.assertEqual(result["resultType"], "complete")

    def test_supported_modern_version_is_served(self) -> None:
        result = session([modern(1, "tools/list")])[1]["result"]
        self.assertEqual(len(result["tools"]), 6)

    def test_unknown_version_is_refused_with_the_specified_error(self) -> None:
        request = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/list",
            "params": {"_meta": {META_VERSION: "1900-01-01", "io.modelcontextprotocol/clientCapabilities": {}}},
        }
        error = session([request])[1]["error"]
        self.assertEqual(error["code"], -32022)
        self.assertEqual(error["message"], "Unsupported protocol version")
        self.assertEqual(error["data"]["requested"], "1900-01-01")
        self.assertIn(MODERN_VERSION, error["data"]["supported"])
        for version in LEGACY_VERSIONS:
            self.assertIn(version, error["data"]["supported"])

    def test_a_legacy_version_in_modern_meta_is_refused(self) -> None:
        request = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/list",
            "params": {"_meta": {META_VERSION: "2025-11-25", "io.modelcontextprotocol/clientCapabilities": {}}},
        }
        self.assertEqual(session([request])[1]["error"]["code"], -32022)


class LegacyEraTest(unittest.TestCase):
    def test_handshake_negotiates_a_real_version(self) -> None:
        request = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-11-25",
                "clientInfo": {"name": "unit-test", "version": "1.0.0"},
                "capabilities": {},
            },
        }
        result = session([request])[1]["result"]
        self.assertEqual(result["protocolVersion"], "2025-11-25")
        self.assertEqual(result["serverInfo"]["name"], "intralogistics-demo")
        self.assertNotIn("resultType", result)

    def test_every_declared_legacy_version_is_honoured(self) -> None:
        requests = [
            {
                "jsonrpc": "2.0",
                "id": index,
                "method": "initialize",
                "params": {"protocolVersion": version, "capabilities": {}, "clientInfo": {"name": "test", "version": "1"}},
            }
            for index, version in enumerate(LEGACY_VERSIONS, start=1)
        ]
        # One process per handshake: a session negotiates once.
        for index, version in enumerate(LEGACY_VERSIONS, start=1):
            result = session([requests[index - 1]])[index]["result"]
            self.assertEqual(result["protocolVersion"], version)

    def test_unknown_legacy_version_receives_a_counter_offer(self) -> None:
        request = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {"protocolVersion": "1999-01-01", "capabilities": {}, "clientInfo": {"name": "test", "version": "1"}},
        }
        self.assertEqual(session([request])[1]["result"]["protocolVersion"], "2025-11-25")

    def test_initialize_at_a_modern_version_is_explained_not_faked(self) -> None:
        request = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {"protocolVersion": MODERN_VERSION},
        }
        error = session([request])[1]["error"]
        self.assertIn("no initialize handshake", error["message"])
        self.assertIn(MODERN_VERSION, error["data"]["supported"])

    def test_tools_work_after_the_handshake(self) -> None:
        requests = [
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {"protocolVersion": "2025-11-25", "capabilities": {}, "clientInfo": {"name": "test", "version": "1"}},
            },
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": {
                    "name": "validate_demo_dataset",
                    "arguments": {"dataset_id": DATASET_ID},
                },
            },
        ]
        result = session(requests)[2]["result"]
        payload = json.loads(result["content"][0]["text"])
        self.assertEqual(payload["status"], "ok")


class StdoutHygieneTest(unittest.TestCase):
    def test_nothing_but_messages_reaches_stdout(self) -> None:
        payload = "\n".join(
            json.dumps(r)
            for r in [
                modern(1, "server/discover"),
                modern(2, "tools/list"),
                {"jsonrpc": "2.0", "method": "notifications/initialized"},
            ]
        )
        process = subprocess.run(
            [sys.executable, str(SERVER)],
            input=payload + "\n",
            capture_output=True,
            text=True,
            timeout=180,
        )
        lines = [line for line in process.stdout.splitlines() if line.strip()]
        self.assertEqual(len(lines), 2)
        for line in lines:
            json.loads(line)

    def test_diagnostics_go_to_stderr(self) -> None:
        request = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-11-25",
                "clientInfo": {"name": "stderr-probe", "version": "1"},
                "capabilities": {},
            },
        }
        process = subprocess.run(
            [sys.executable, str(SERVER)],
            input=json.dumps(request) + "\n",
            capture_output=True,
            text=True,
            timeout=180,
        )
        self.assertIn("stderr-probe", process.stderr)
        self.assertNotIn("stderr-probe", process.stdout)


class ManifestConsistencyTest(unittest.TestCase):
    """One server, one command, whatever runtime starts it."""

    def setUp(self) -> None:
        self.mcp_config = json.loads(MCP_CONFIG.read_text(encoding="utf-8"))
        self.codex = json.loads(CODEX_MANIFEST.read_text(encoding="utf-8"))
        self.claude = json.loads(CLAUDE_MANIFEST.read_text(encoding="utf-8"))

    def test_the_shared_mcp_config_exists_and_names_one_server(self) -> None:
        servers = self.mcp_config["mcpServers"]
        self.assertEqual(list(servers), ["intralogistics-demo"])

    def test_the_codex_manifest_points_at_the_shared_config(self) -> None:
        self.assertEqual(self.codex["mcpServers"], "./.mcp.json")

    def test_every_referenced_path_exists(self) -> None:
        shared = self.mcp_config["mcpServers"]["intralogistics-demo"]
        target = (ROOT / shared["args"][0]).resolve()
        self.assertTrue(target.is_file(), msg=f"{target} does not exist")
        self.assertEqual(target, SERVER.resolve())
        self.assertTrue((ROOT / self.codex["skills"].lstrip("./")).is_dir())
        for skill in self.claude.get("skills", ["./skills"]):
            self.assertTrue((ROOT / skill.lstrip("./")).is_dir(), msg=skill)

    def test_codex_and_claude_start_the_same_logical_server(self) -> None:
        shared = self.mcp_config["mcpServers"]["intralogistics-demo"]
        claude_server = self.claude["mcpServers"]["intralogistics-demo"]
        self.assertEqual(shared["command"], claude_server["command"])
        # Claude resolves the plugin root through its own variable; strip it and
        # the two runtimes must land on the same file.
        claude_target = claude_server["args"][0].replace("${CLAUDE_PLUGIN_ROOT}/", "")
        shared_target = shared["args"][0].lstrip("./")
        self.assertEqual(claude_target, shared_target)
        self.assertTrue((ROOT / claude_target).is_file())

    def test_both_manifests_declare_the_same_name_and_version(self) -> None:
        for key in ("name", "version", "description", "license"):
            self.assertEqual(self.codex[key], self.claude[key], msg=key)

    def test_the_server_starts_from_the_repository_root(self) -> None:
        shared = self.mcp_config["mcpServers"]["intralogistics-demo"]
        process = subprocess.run(
            [sys.executable, shared["args"][0]],
            input=json.dumps(modern(1, "server/discover")) + "\n",
            capture_output=True,
            text=True,
            cwd=str(ROOT),
            timeout=180,
        )
        self.assertEqual(process.returncode, 0, msg=process.stderr)
        self.assertIn("supportedVersions", process.stdout)


@unittest.skipUnless(SDK_AVAILABLE, "the official MCP SDK is not installed")
class SdkInteroperabilityTest(unittest.TestCase):
    """Drive the server with the official client, not with our own assumptions."""

    def test_the_sdk_version_registry_matches_what_the_server_declares(self) -> None:
        self.assertEqual(tuple(MODERN_PROTOCOL_VERSIONS), (MODERN_VERSION,))
        self.assertEqual(set(HANDSHAKE_PROTOCOL_VERSIONS), set(LEGACY_VERSIONS))

    def _run(self, mode: str):
        import asyncio

        from mcp import Client, StdioServerParameters

        async def main():
            parameters = StdioServerParameters(
                command=sys.executable, args=[str(SERVER)], cwd=str(ROOT)
            )
            async with Client(parameters, mode=mode) as client:
                tools = await client.list_tools()
                result = await client.call_tool(
                    "validate_demo_dataset", {"dataset_id": DATASET_ID}
                )
                return tools, result

        return asyncio.run(main())

    def test_official_client_auto_mode_reaches_the_modern_era(self) -> None:
        tools, result = self._run("auto")
        names = sorted(tool.name for tool in tools.tools)
        self.assertEqual(len(names), 6)
        self.assertIn("validate_demo_dataset", names)
        payload = json.loads(result.content[0].text)
        self.assertEqual(payload["status"], "ok")

    def test_official_client_legacy_mode_reaches_the_handshake_era(self) -> None:
        tools, result = self._run("legacy")
        self.assertEqual(len(tools.tools), 6)
        payload = json.loads(result.content[0].text)
        self.assertEqual(payload["status"], "ok")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
