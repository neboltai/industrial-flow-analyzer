#!/usr/bin/env python3
"""Smoke test for the local demonstration MCP server.

Runs the real server as a subprocess over stdio and checks both protocol eras
against the behaviour the specification requires, plus the safety properties the
Community Edition promises.

Protocol
    modern ``server/discover``; modern ``tools/list`` and ``tools/call``;
    an unknown modern version refused with ``-32022`` listing the supported
    versions; the legacy ``initialize`` handshake with a real ``protocolVersion``,
    ``clientInfo`` and ``capabilities``; a legacy ``initialize`` naming an unknown
    version counter-offered; ``initialize`` at a modern version refused with an
    explanation.

Tools
    each of the six tools returns a usable result; an unknown tool, a wrong
    argument type, an out-of-range ``top``, a malformed request and an invalid
    move are all refused.

Safety
    an unknown ``dataset_id`` and anything path-shaped are refused; the dataset
    file is byte-identical afterwards; stdout carries nothing but MCP messages.

Exit code 0 means every check passed.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

HERE = Path(__file__).resolve().parent
SERVER = HERE / "server.py"
MOCK_DATASET = HERE / "mock_data" / "fictional-small-warehouse" / "dataset.json"

META_VERSION = "io.modelcontextprotocol/protocolVersion"
META_CLIENT_INFO = "io.modelcontextprotocol/clientInfo"
META_CLIENT_CAPABILITIES = "io.modelcontextprotocol/clientCapabilities"

MODERN_VERSION = "2026-07-28"
LEGACY_VERSION = "2025-11-25"
UNSUPPORTED_PROTOCOL_VERSION_CODE = -32022

EXPECTED_TOOLS = [
    "analyze_demo_flows",
    "get_demo_summary",
    "list_demo_datasets",
    "recommend_demo_slotting",
    "simulate_demo_moves",
    "validate_demo_dataset",
]

DATASET_ID = "fictional-small-warehouse"


class SmokeFailure(AssertionError):
    pass


def check(condition: bool, message: str) -> None:
    if not condition:
        raise SmokeFailure(message)


def modern_meta() -> Dict[str, Any]:
    return {
        META_VERSION: MODERN_VERSION,
        META_CLIENT_INFO: {"name": "ifa-smoke-test", "version": "0.1.2"},
        META_CLIENT_CAPABILITIES: {},
    }


def run_session(requests: List[Dict[str, Any]]) -> tuple:
    """Return ``(responses, stdout_lines, stderr)`` for one server process."""
    payload = "\n".join(json.dumps(request) for request in requests) + "\n"
    process = subprocess.run(
        [sys.executable, str(SERVER)],
        input=payload,
        capture_output=True,
        text=True,
        timeout=180,
    )
    if process.returncode != 0:
        raise SmokeFailure(
            f"server exited with {process.returncode}\nstderr:\n{process.stderr}"
        )
    lines = [line for line in process.stdout.splitlines() if line.strip()]
    responses = []
    for line in lines:
        try:
            responses.append(json.loads(line))
        except json.JSONDecodeError as exc:
            raise SmokeFailure(
                f"stdout carried a line that is not a JSON-RPC message: {line!r} ({exc})"
            ) from exc
    return responses, lines, process.stderr


def payload_of(response: Dict[str, Any]) -> Dict[str, Any]:
    check("result" in response, f"expected a result, got {response}")
    content = response["result"]["content"]
    return json.loads(content[0]["text"])


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def modern_call(request_id: int, name: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "method": "tools/call",
        "params": {"_meta": modern_meta(), "name": name, "arguments": arguments},
    }


def main() -> int:
    check(SERVER.is_file(), f"server not found at {SERVER}")
    check(MOCK_DATASET.is_file(), f"mock dataset not found at {MOCK_DATASET}")
    before = digest(MOCK_DATASET)
    passed: List[str] = []

    # ================================================================ modern
    modern_requests: List[Dict[str, Any]] = [
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "server/discover",
            "params": {"_meta": modern_meta()},
        },
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {"_meta": modern_meta()}},
        modern_call(3, "list_demo_datasets", {}),
        modern_call(4, "get_demo_summary", {"dataset_id": DATASET_ID}),
        modern_call(5, "validate_demo_dataset", {"dataset_id": DATASET_ID}),
        modern_call(6, "analyze_demo_flows", {"dataset_id": DATASET_ID, "top": 5}),
        modern_call(7, "recommend_demo_slotting", {"dataset_id": DATASET_ID, "top": 5}),
        modern_call(
            8,
            "simulate_demo_moves",
            {
                "dataset_id": DATASET_ID,
                "moves": [{"type": "pair_swap", "sku_a": "SKU-1001", "sku_b": "SKU-1004"}],
            },
        ),
        # Refusals
        modern_call(9, "get_demo_summary", {"dataset_id": "does-not-exist"}),
        modern_call(10, "validate_demo_dataset", {"dataset_id": "../../../etc/passwd"}),
        modern_call(11, "delete_everything", {}),
        modern_call(
            12,
            "simulate_demo_moves",
            {
                "dataset_id": DATASET_ID,
                "moves": [{"type": "teleport", "sku_a": "SKU-1001", "sku_b": "SKU-1004"}],
            },
        ),
        modern_call(13, "get_demo_summary", {"dataset_id": 42}),
        modern_call(14, "analyze_demo_flows", {"dataset_id": DATASET_ID, "top": 999}),
        modern_call(15, "get_demo_summary", {}),
        # Unknown protocol version
        {
            "jsonrpc": "2.0",
            "id": 16,
            "method": "tools/list",
            "params": {"_meta": {META_VERSION: "1900-01-01", "io.modelcontextprotocol/clientCapabilities": {}}},
        },
        # Malformed requests
        {"jsonrpc": "1.0", "id": 17, "method": "tools/list"},
        {"jsonrpc": "2.0", "id": 18, "method": "tools/list", "params": "not-an-object"},
        {"jsonrpc": "2.0", "id": 19, "method": "no/such/method", "params": {"_meta": modern_meta()}},
    ]
    responses, stdout_lines, stderr = run_session(modern_requests)
    by_id = {r.get("id"): r for r in responses}

    discover = by_id[1]["result"]
    check(
        discover["supportedVersions"] == [MODERN_VERSION],
        f"server/discover advertises {discover.get('supportedVersions')}",
    )
    check("tools" in discover["capabilities"], "server/discover omits the tools capability")
    server_info = discover.get("_meta", {}).get("io.modelcontextprotocol/serverInfo", {})
    check(server_info.get("name") == "intralogistics-demo", "unexpected serverInfo name")
    check(bool(server_info.get("version")), "serverInfo carries no version")
    check(discover.get("resultType") == "complete", "modern result lacks resultType")
    passed.append("modern server/discover")

    names = sorted(tool["name"] for tool in by_id[2]["result"]["tools"])
    check(names == EXPECTED_TOOLS, f"expected {EXPECTED_TOOLS}, got {names}")
    for tool in by_id[2]["result"]["tools"]:
        check(bool(tool.get("description")), f"{tool['name']} has no description")
        check("inputSchema" in tool, f"{tool['name']} has no input schema")
    check(by_id[2]["result"].get("resultType") == "complete", "tools/list lacks resultType")
    passed.append("modern tools/list")

    listing = payload_of(by_id[3])
    check(
        DATASET_ID in [entry["dataset_id"] for entry in listing["datasets"]],
        "the demo dataset is not listed",
    )
    check(all(entry["fictional"] for entry in listing["datasets"]), "a dataset is not fictional")
    passed.append("list_demo_datasets")

    summary = payload_of(by_id[4])
    check(summary["orders"] > 0, "summary reports no order")
    check(summary["pick_locations"] > 0, "summary reports no pick location")
    check(summary.get("sequence_basis") is not None, "summary omits sequence_basis")
    passed.append("get_demo_summary")

    validation = payload_of(by_id[5])
    check(validation["status"] == "ok", f"demo dataset is not valid: {validation['status']}")
    check(validation["exit_code"] == 0, "validation exit code is not 0")
    passed.append("validate_demo_dataset")

    analysis = payload_of(by_id[6])
    check(analysis["metrics"]["total_distance_m"] > 0, "analysis reports no distance")
    check(bool(analysis["abc"]), "analysis reports no ABC classification")
    check(bool(analysis["edge_flows"]), "analysis reports no edge flow")
    check(analysis.get("distance_basis") is not None, "analysis omits distance_basis")
    passed.append("analyze_demo_flows")

    recommendations = payload_of(by_id[7])
    check(
        recommendations["measure"] == "estimated pick-distance reduction",
        "the recommendation measure is not an estimate",
    )
    for recommendation in recommendations["recommendations"]:
        check(
            all(value == "pass" for value in recommendation["constraint_checks"].values()),
            f"{recommendation['recommendation_id']} has a non-passing constraint check",
        )
    passed.append("recommend_demo_slotting")

    simulation = payload_of(by_id[8])
    check(simulation["constraint_status"] == "ok", "simulated move is not constraint-safe")
    check(simulation["estimated_reduction_m"] > 0, "simulation reports no reduction")
    check(simulation["source_dataset_modified"] is False, "server claims it modified the source")
    passed.append("simulate_demo_moves")

    for request_id, needle, label in (
        (9, "Unknown dataset_id", "unknown dataset_id refused"),
        (10, "Unknown dataset_id", "arbitrary path refused"),
        (12, "pair_swap", "unsupported move refused"),
        (13, "must be a string", "wrong argument type refused"),
        (14, "at most 50", "out-of-range top refused"),
        (15, "Missing required argument", "missing argument refused"),
    ):
        response = by_id[request_id]
        check(response["result"]["isError"] is True, f"{label}: not reported as an error")
        message = json.dumps(payload_of(response))
        check(needle in message, f"{label}: unhelpful message {message[:120]}")
        passed.append(label)

    unknown_tool = payload_of(by_id[11])
    check(by_id[11]["result"]["isError"] is True, "an unknown tool did not error")
    check(unknown_tool["error"] == "unknown_tool", "unexpected error kind for unknown tool")
    passed.append("unknown tool refused")

    version_error = by_id[16]["error"]
    check(
        version_error["code"] == UNSUPPORTED_PROTOCOL_VERSION_CODE,
        f"unknown version refused with {version_error['code']}, expected "
        f"{UNSUPPORTED_PROTOCOL_VERSION_CODE}",
    )
    check(
        MODERN_VERSION in version_error["data"]["supported"],
        "the version error does not list the supported versions",
    )
    check(version_error["data"]["requested"] == "1900-01-01", "the error omits the request")
    passed.append("unknown protocol version refused with -32022")

    check(by_id[17]["error"]["code"] == -32600, "a bad jsonrpc field was not refused")
    check(by_id[18]["error"]["code"] == -32602, "non-object params were not refused")
    check(by_id[19]["error"]["code"] == -32601, "an unknown method was not refused")
    passed.append("malformed requests refused")

    check(
        len(stdout_lines) == len(responses),
        "stdout carried non-message output alongside the JSON-RPC messages",
    )
    passed.append("stdout carries MCP messages only")

    # ================================================================ legacy
    legacy_requests: List[Dict[str, Any]] = [
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": LEGACY_VERSION,
                "clientInfo": {"name": "legacy-smoke-client", "version": "1.0.0"},
                "capabilities": {"roots": {}},
            },
        },
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
        {
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {"name": "get_demo_summary", "arguments": {"dataset_id": DATASET_ID}},
        },
        {
            "jsonrpc": "2.0",
            "id": 4,
            "method": "initialize",
            "params": {"protocolVersion": "1999-01-01", "capabilities": {}, "clientInfo": {"name": "old", "version": "1"}},
        },
        {
            "jsonrpc": "2.0",
            "id": 5,
            "method": "initialize",
            "params": {"protocolVersion": MODERN_VERSION},
        },
        {
            "jsonrpc": "2.0",
            "id": 6,
            "method": "initialize",
            "params": {"protocolVersion": 20251125},
        },
    ]
    legacy_responses, legacy_lines, _ = run_session(legacy_requests)
    legacy_by_id = {r.get("id"): r for r in legacy_responses}

    handshake = legacy_by_id[1]["result"]
    check(
        handshake["protocolVersion"] == LEGACY_VERSION,
        f"handshake negotiated {handshake['protocolVersion']}, expected {LEGACY_VERSION}",
    )
    check("tools" in handshake["capabilities"], "handshake omits the tools capability")
    check(handshake["serverInfo"]["name"] == "intralogistics-demo", "unexpected server name")
    check("resultType" not in handshake, "a legacy result must not carry resultType")
    passed.append("legacy initialize handshake")

    check(
        sorted(t["name"] for t in legacy_by_id[2]["result"]["tools"]) == EXPECTED_TOOLS,
        "legacy tools/list does not expose the six tools",
    )
    legacy_summary = payload_of(legacy_by_id[3])
    check(legacy_summary["orders"] > 0, "legacy tools/call returned nothing usable")
    passed.append("legacy tools/list and tools/call")

    counter_offer = legacy_by_id[4]["result"]["protocolVersion"]
    check(
        counter_offer == LEGACY_VERSION,
        f"an unknown legacy version was answered with {counter_offer}",
    )
    passed.append("legacy unknown version counter-offered")

    modern_via_initialize = legacy_by_id[5]["error"]
    check(
        "no initialize handshake" in modern_via_initialize["message"],
        "initialize at a modern version was not explained",
    )
    check(
        MODERN_VERSION in modern_via_initialize["data"]["supported"],
        "the refusal does not name the supported versions",
    )
    passed.append("initialize at a modern version refused with an explanation")

    malformed = legacy_by_id[6]["error"]
    check(malformed["code"] == -32602, "a non-string protocolVersion was not refused")
    check(
        "supported" in malformed.get("data", {}),
        "a legacy client was refused without being told what is supported",
    )
    passed.append("malformed legacy protocolVersion refused")

    check(
        len(legacy_lines) == len(legacy_responses),
        "stdout carried non-message output during the legacy session",
    )

    # =============================================================== safety
    check(digest(MOCK_DATASET) == before, "the mock dataset file changed during the session")
    passed.append("dataset unchanged on disk")

    print(f"MCP smoke test: {len(passed)} checks passed")
    for name in passed:
        print(f"  ok  {name}")
    if stderr.strip():
        print(f"  (server logged {len(stderr.strip().splitlines())} line(s) to stderr, as allowed)")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SmokeFailure as failure:
        print(f"MCP smoke test FAILED: {failure}", file=sys.stderr)
        raise SystemExit(1)
