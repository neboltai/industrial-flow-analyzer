#!/usr/bin/env python3
"""Local demonstration MCP server for the Community Edition.

Transport: JSON-RPC 2.0 over stdio, one JSON object per line. Nothing but MCP
messages is ever written to stdout; diagnostics go to stderr.

Dual-era protocol support
-------------------------
The server answers both protocol eras defined by the specification, and the two
sets are exactly those the official Python SDK speaks:

* **modern** (``2026-07-28``): no handshake. Every request carries its version in
  ``params._meta['io.modelcontextprotocol/protocolVersion']``. ``server/discover``
  is implemented, as the specification requires. A version this server does not
  implement is refused with ``UnsupportedProtocolVersionError`` (JSON-RPC code
  ``-32022``) whose ``data.supported`` lists what it does implement.
* **legacy** (``2025-11-25``, ``2025-06-18``, ``2025-03-26``, ``2024-11-05``):
  the ``initialize`` handshake. An ``initialize`` naming an unknown version is
  answered with the newest legacy version this server supports, which is what
  the handshake is for; an ``initialize`` with a malformed ``protocolVersion``
  is refused and the error names the supported versions, because a legacy client
  has no fall-forward mechanism.

Which era serves a request is decided by how the client opens, exactly as the
specification's dual-era server rules describe: a request carrying modern
``_meta`` is served statelessly under the modern revision; an ``initialize``
selects legacy semantics for the life of the process.

References:
https://modelcontextprotocol.io/specification/2026-07-28/basic/versioning
https://modelcontextprotocol.io/specification/2026-07-28/server/discover

Scope, deliberately narrow:

* it serves only the fictional datasets under ``mcp/mock_data``;
* every tool takes a ``dataset_id`` that must appear in a locally built
  allowlist, so no tool ever accepts a path chosen by the model;
* there is no network access, no WMS or ERP connector, no secret, no endpoint,
  and no write to any external system;
* ``simulate_demo_moves`` works on an in-memory copy and never modifies the
  dataset on disk.

Six tools are exposed:

    list_demo_datasets
    get_demo_summary
    validate_demo_dataset
    analyze_demo_flows
    recommend_demo_slotting
    simulate_demo_moves
"""

from __future__ import annotations

import json
import re
import traceback
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC = REPO_ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from intralogistics_flow_analyzer.affinity import compute_affinities  # noqa: E402
from intralogistics_flow_analyzer.classification import abc_classify, xyz_classify  # noqa: E402
from intralogistics_flow_analyzer.graph import build_graph  # noqa: E402
from intralogistics_flow_analyzer.loaders import load_dataset  # noqa: E402
from intralogistics_flow_analyzer.metrics import (  # noqa: E402
    compute_metrics,
    data_quality,
    edge_flows,
    location_metrics,
    sku_metrics,
    top_locations_by_distance,
    top_orders_by_distance,
)
from intralogistics_flow_analyzer.models import ENGINE_VERSION, Dataset  # noqa: E402
from intralogistics_flow_analyzer.routing import build_routes  # noqa: E402
from intralogistics_flow_analyzer.simulation import (  # noqa: E402
    SimulationError,
    parse_moves,
    simulate_moves,
)
from intralogistics_flow_analyzer.slotting import recommend  # noqa: E402
from intralogistics_flow_analyzer.validation import validate  # noqa: E402

MOCK_DATA_DIR = Path(__file__).resolve().parent / "mock_data"
SERVER_NAME = "intralogistics-demo"

#: Revisions that use the stateless per-request envelope.
MODERN_PROTOCOL_VERSIONS = ("2026-07-28",)
#: Revisions reachable through the ``initialize`` handshake, newest first.
LEGACY_PROTOCOL_VERSIONS = ("2025-11-25", "2025-06-18", "2025-03-26", "2024-11-05")
SUPPORTED_PROTOCOL_VERSIONS = MODERN_PROTOCOL_VERSIONS + LEGACY_PROTOCOL_VERSIONS

LATEST_MODERN_VERSION = MODERN_PROTOCOL_VERSIONS[0]
LATEST_LEGACY_VERSION = LEGACY_PROTOCOL_VERSIONS[0]

#: Reserved ``_meta`` keys of the modern era.
META_PROTOCOL_VERSION = "io.modelcontextprotocol/protocolVersion"
META_CLIENT_INFO = "io.modelcontextprotocol/clientInfo"
META_CLIENT_CAPABILITIES = "io.modelcontextprotocol/clientCapabilities"
META_SERVER_INFO = "io.modelcontextprotocol/serverInfo"

JSONRPC_PARSE_ERROR = -32700
JSONRPC_INVALID_REQUEST = -32600
JSONRPC_METHOD_NOT_FOUND = -32601
JSONRPC_INVALID_PARAMS = -32602
JSONRPC_INTERNAL_ERROR = -32603
#: UnsupportedProtocolVersionError, specification 2026-07-28.
JSONRPC_UNSUPPORTED_PROTOCOL_VERSION = -32022

SERVER_CAPABILITIES = {"tools": {"listChanged": False}}

#: Caching hints required on cacheable modern results (``server/discover`` and
#: ``tools/list``). Both lists are static for the life of the process and hold no
#: caller-specific data, so the scope is public. See
#: https://modelcontextprotocol.io/specification/2026-07-28/server/utilities/caching
CACHE_SCOPE = "public"
DISCOVER_TTL_MS = 3_600_000
TOOLS_LIST_TTL_MS = 300_000

INSTRUCTIONS = (
    "Read-only demonstration server for the Intralogistics Flow & Slotting Analyzer "
    "Community Edition. It serves fictional datasets only, accepts no file path, reaches "
    "no network and writes to no external system. To analyse real warehouse data, run the "
    "'ifa' command line locally on your own export."
)


def log(message: str) -> None:
    """Diagnostics go to stderr; stdout carries MCP messages only."""
    print(f"[{SERVER_NAME}] {message}", file=sys.stderr, flush=True)


class ToolError(ValueError):
    """A tool-level error reported back to the client as an error result."""


def build_allowlist() -> Dict[str, Path]:
    """Scan ``mcp/mock_data`` once and build the only paths this server will read."""
    allowlist: Dict[str, Path] = {}
    if not MOCK_DATA_DIR.is_dir():
        return allowlist
    for directory in sorted(p for p in MOCK_DATA_DIR.iterdir() if p.is_dir()):
        candidate = directory / "dataset.json"
        if candidate.is_file():
            allowlist[directory.name] = candidate
    return allowlist


ALLOWLIST: Dict[str, Path] = build_allowlist()
_CACHE: Dict[str, Dataset] = {}


def resolve_dataset(dataset_id: Any) -> Dataset:
    """Load a dataset by identifier. Any value outside the allowlist is refused."""
    if not isinstance(dataset_id, str) or not dataset_id:
        raise ToolError("dataset_id must be a non-empty string.")
    path = ALLOWLIST.get(dataset_id)
    if path is None:
        raise ToolError(
            f"Unknown dataset_id {dataset_id!r}. This server only serves the fictional "
            f"datasets {', '.join(sorted(ALLOWLIST)) or '(none installed)'}. "
            "It never reads a path supplied by the caller."
        )
    if dataset_id not in _CACHE:
        _CACHE[dataset_id] = load_dataset(path)
    # A fresh copy per call: no tool can mutate the cached dataset.
    return _CACHE[dataset_id].copy()


TOOLS: List[Dict[str, Any]] = [
    {
        "name": "list_demo_datasets",
        "description": (
            "List the fictional demonstration datasets available locally. Returns the "
            "dataset_id values accepted by every other tool."
        ),
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "get_demo_summary",
        "description": (
            "Summarise a fictional dataset: period, layout size, item and order counts. "
            "Read-only."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {"dataset_id": {"type": "string"}},
            "required": ["dataset_id"],
            "additionalProperties": False,
        },
    },
    {
        "name": "validate_demo_dataset",
        "description": (
            "Run the deterministic validation and return the status and every issue. "
            "Nothing is repaired."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {"dataset_id": {"type": "string"}},
            "required": ["dataset_id"],
            "additionalProperties": False,
        },
    },
    {
        "name": "analyze_demo_flows",
        "description": (
            "Reconstruct routes and return flow metrics, edge traversals, leading "
            "locations, ABC/XYZ classes and co-pick affinities. Read-only."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "dataset_id": {"type": "string"},
                "top": {"type": "integer", "minimum": 1, "maximum": 50},
            },
            "required": ["dataset_id"],
            "additionalProperties": False,
        },
    },
    {
        "name": "recommend_demo_slotting",
        "description": (
            "Return constraint-checked, individually simulated pair swaps with their "
            "estimated pick-distance reduction, plus the rejected candidates and why "
            "each was rejected. Proposes nothing when a critical constraint is unknown."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "dataset_id": {"type": "string"},
                "top": {"type": "integer", "minimum": 1, "maximum": 50},
            },
            "required": ["dataset_id"],
            "additionalProperties": False,
        },
    },
    {
        "name": "simulate_demo_moves",
        "description": (
            "Replay the historical orders against an explicit list of pair swaps and "
            "return the before/after comparison. Works on an in-memory copy; the "
            "dataset on disk is never modified."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "dataset_id": {"type": "string"},
                "moves": {
                    "type": "array",
                    "minItems": 1,
                    "items": {
                        "type": "object",
                        "properties": {
                            "type": {"type": "string", "const": "pair_swap"},
                            "sku_a": {"type": "string"},
                            "sku_b": {"type": "string"},
                        },
                        "required": ["type", "sku_a", "sku_b"],
                        "additionalProperties": False,
                    },
                },
            },
            "required": ["dataset_id", "moves"],
            "additionalProperties": False,
        },
    },
]


# ------------------------------------------------------------------ tools


def tool_list_demo_datasets(_: Dict[str, Any]) -> Dict[str, Any]:
    datasets = []
    for dataset_id in sorted(ALLOWLIST):
        dataset = resolve_dataset(dataset_id)
        datasets.append(
            {
                "dataset_id": dataset_id,
                "site_id": dataset.meta.site_id,
                "fictional": dataset.meta.fictional,
                "period_start": dataset.meta.period_start,
                "period_end": dataset.meta.period_end,
                "orders": len(dataset.orders()),
                "pick_lines": len(dataset.picks),
            }
        )
    return {
        "engine_version": ENGINE_VERSION,
        "datasets": datasets,
        "note": (
            "Community Edition demonstration server. Only these fictional datasets are "
            "reachable; no path, connector or external system is accessible."
        ),
    }


def tool_get_demo_summary(arguments: Dict[str, Any]) -> Dict[str, Any]:
    dataset = resolve_dataset(arguments.get("dataset_id"))
    graph = build_graph(dataset.nodes, dataset.edges)
    return {
        "dataset_id": dataset.meta.dataset_id,
        "fictional": dataset.meta.fictional,
        "period_start": dataset.meta.period_start,
        "period_end": dataset.meta.period_end,
        "time_zone": dataset.meta.time_zone,
        "sequence_basis": dataset.meta.sequence_basis,
        "distance_basis": dataset.meta.distance_basis,
        "nodes": len(dataset.nodes),
        "edges": len(dataset.edges),
        "travel_modes": graph.modes(),
        "locations": len(dataset.locations),
        "pick_locations": len([l for l in dataset.locations if l.location_type == "pick"]),
        "items": len(dataset.items),
        "orders": len(dataset.orders()),
        "pick_lines": len(dataset.picks),
        "zones": sorted({l.zone_id for l in dataset.locations if l.zone_id}),
    }


def tool_validate_demo_dataset(arguments: Dict[str, Any]) -> Dict[str, Any]:
    dataset = resolve_dataset(arguments.get("dataset_id"))
    report = validate(dataset)
    document = report.to_dict()
    document["dataset_id"] = dataset.meta.dataset_id
    document["exit_code"] = report.exit_code()
    return document


def tool_analyze_demo_flows(arguments: Dict[str, Any]) -> Dict[str, Any]:
    dataset = resolve_dataset(arguments.get("dataset_id"))
    top = int(arguments.get("top") or 10)
    graph = build_graph(dataset.nodes, dataset.edges)
    report = validate(dataset, graph)
    routing = build_routes(dataset, graph)
    locations = location_metrics(dataset, routing, graph)
    xyz_rows, xyz_marker = xyz_classify(dataset)
    return {
        "dataset_id": dataset.meta.dataset_id,
        "status": report.status,
        "sequence_basis": dataset.meta.sequence_basis,
        "distance_basis": dataset.meta.distance_basis,
        "data_quality": data_quality(dataset, routing),
        "metrics": compute_metrics(dataset, routing, graph),
        "edge_flows": edge_flows(dataset, routing)[:top],
        "top_locations_by_distance": top_locations_by_distance(locations, top),
        "top_orders_by_distance": top_orders_by_distance(routing, top),
        "sku_metrics": sku_metrics(dataset, routing, graph),
        "abc": abc_classify(dataset),
        "xyz": xyz_rows,
        "xyz_marker": xyz_marker,
        "affinities": compute_affinities(dataset)[:top],
        "note": (
            "Distances are modelled: the shortest permitted path on the declared aisle graph "
            "between consecutive pick access nodes, in the given sequence. They are not a "
            "measurement of the path actually walked. A high frequency or a long modelled "
            "route is not by itself evidence of a problem."
        ),
    }


def tool_recommend_demo_slotting(arguments: Dict[str, Any]) -> Dict[str, Any]:
    dataset = resolve_dataset(arguments.get("dataset_id"))
    top = int(arguments.get("top") or 10)
    graph = build_graph(dataset.nodes, dataset.edges)
    report = validate(dataset, graph)
    if report.status == "data_error":
        return {
            "dataset_id": dataset.meta.dataset_id,
            "status": report.status,
            "recommendations": [],
            "note": "Dataset status is data_error; no recommendation is produced.",
            "issues": [i.to_dict() for i in report.errors],
        }
    routing = build_routes(dataset, graph)
    result = recommend(dataset, graph, routing, top)
    document = result.to_dict()
    document["dataset_id"] = dataset.meta.dataset_id
    document["status"] = report.status
    document["measure"] = "estimated pick-distance reduction"
    return document


def tool_simulate_demo_moves(arguments: Dict[str, Any]) -> Dict[str, Any]:
    dataset_id = arguments.get("dataset_id")
    dataset = resolve_dataset(dataset_id)
    graph = build_graph(dataset.nodes, dataset.edges)
    try:
        moves = parse_moves({"moves": arguments.get("moves")})
    except SimulationError as exc:
        raise ToolError(str(exc)) from exc
    try:
        comparison = simulate_moves(dataset, graph, moves)
    except SimulationError as exc:
        raise ToolError(str(exc)) from exc
    document = comparison.to_dict()
    document["dataset_id"] = dataset.meta.dataset_id
    document["source_dataset_modified"] = False
    return document


HANDLERS = {
    "list_demo_datasets": tool_list_demo_datasets,
    "get_demo_summary": tool_get_demo_summary,
    "validate_demo_dataset": tool_validate_demo_dataset,
    "analyze_demo_flows": tool_analyze_demo_flows,
    "recommend_demo_slotting": tool_recommend_demo_slotting,
    "simulate_demo_moves": tool_simulate_demo_moves,
}


# ----------------------------------------------------------------- JSON-RPC


def _result(request_id: Any, payload: Dict[str, Any], modern: bool) -> Dict[str, Any]:
    """A JSON-RPC result. The modern era tags every result with ``resultType``."""
    body = dict(payload)
    if modern:
        body.setdefault("resultType", "complete")
    return {"jsonrpc": "2.0", "id": request_id, "result": body}


def _error(
    request_id: Any, code: int, message: str, data: Optional[Dict[str, Any]] = None
) -> Dict[str, Any]:
    error: Dict[str, Any] = {"code": code, "message": message}
    if data is not None:
        error["data"] = data
    return {"jsonrpc": "2.0", "id": request_id, "error": error}


def _unsupported_version_error(request_id: Any, requested: Any) -> Dict[str, Any]:
    """UnsupportedProtocolVersionError as specified for revision 2026-07-28."""
    return _error(
        request_id,
        JSONRPC_UNSUPPORTED_PROTOCOL_VERSION,
        "Unsupported protocol version",
        {
            "supported": list(SUPPORTED_PROTOCOL_VERSIONS),
            "requested": requested,
        },
    )


def _tool_result(payload: Dict[str, Any], is_error: bool = False) -> Dict[str, Any]:
    return {
        "content": [{"type": "text", "text": json.dumps(payload, ensure_ascii=False, indent=2)}],
        "isError": is_error,
    }


def _valid_version(value: Any) -> bool:
    return isinstance(value, str) and re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", value) is not None


def _valid_implementation(value: Any) -> bool:
    return isinstance(value, dict) and all(isinstance(value.get(k), str) for k in ("name", "version"))


def _discover_result() -> Dict[str, Any]:
    return {
        "supportedVersions": list(MODERN_PROTOCOL_VERSIONS),
        "capabilities": dict(SERVER_CAPABILITIES),
        "instructions": INSTRUCTIONS,
        "ttlMs": DISCOVER_TTL_MS,
        "cacheScope": CACHE_SCOPE,
        "_meta": {META_SERVER_INFO: {"name": SERVER_NAME, "version": ENGINE_VERSION}},
    }


def _validate_tool_arguments(name: str, arguments: Dict[str, Any]) -> None:
    """Reject wrong types and out-of-range values before any work is done."""
    schema = next((tool["inputSchema"] for tool in TOOLS if tool["name"] == name), None)
    if schema is None:  # pragma: no cover - guarded by the caller
        return
    _validate_value(arguments, schema, "arguments")


def _validate_value(value: Any, rule: Dict[str, Any], path: str) -> None:
    """Validate the closed subset of JSON Schema used by our tool contracts.

    No external schemas or network resolution. Contract parity with Draft
    2020-12 is tested independently using jsonschema in the development suite.
    """
    expected = rule.get("type")
    valid = {
        "object": isinstance(value, dict),
        "array": isinstance(value, list),
        "string": isinstance(value, str),
        "integer": isinstance(value, int) and not isinstance(value, bool),
    }
    if expected not in valid or not valid[expected]:
        raise ToolError(f"{path} must be a {expected}.")
    if "const" in rule and value != rule["const"]:
        raise ToolError(f"{path} must equal {rule['const']!r}.")
    if expected == "object":
        properties = rule.get("properties", {})
        for key in rule.get("required", []):
            if key not in value:
                raise ToolError(f"Missing required argument {key!r} in {path}.")
        if rule.get("additionalProperties") is False and set(value) - set(properties):
            raise ToolError(f"{path} contains unknown properties.")
        for key, child in value.items():
            if key in properties:
                _validate_value(child, properties[key], f"{path}.{key}")
    elif expected == "array":
        if len(value) < rule.get("minItems", 0):
            raise ToolError(f"{path} contains too few items.")
        for index, child in enumerate(value):
            _validate_value(child, rule["items"], f"{path}[{index}]")
    elif expected == "integer":
        if "minimum" in rule and value < rule["minimum"]:
            raise ToolError(f"{path} must be at least {rule['minimum']}.")
        if "maximum" in rule and value > rule["maximum"]:
            raise ToolError(f"{path} must be at most {rule['maximum']}.")


def _handle_tools_call(request_id: Any, params: Dict[str, Any], modern: bool) -> Dict[str, Any]:
    name = params.get("name")
    arguments = params.get("arguments", {})
    handler = HANDLERS.get(name) if isinstance(name, str) else None
    if handler is None:
        return _result(
            request_id,
            _tool_result(
                {
                    "error": "unknown_tool",
                    "message": f"Unknown tool {name!r}.",
                    "available_tools": sorted(HANDLERS),
                },
                is_error=True,
            ),
            modern,
        )
    if not isinstance(arguments, dict):
        return _error(request_id, JSONRPC_INVALID_PARAMS, "arguments must be an object.")
    try:
        _validate_tool_arguments(str(name), arguments)
        payload = handler(arguments)
    except ToolError as exc:
        return _result(
            request_id,
            _tool_result({"error": "invalid_request", "message": str(exc)}, is_error=True),
            modern,
        )
    except Exception as exc:  # noqa: BLE001 - never crash the transport
        log(f"tool {name} failed: {exc}")
        traceback.print_exc(file=sys.stderr)
        return _result(
            request_id,
            _tool_result({"error": "internal_error", "message": "Internal tool error."}, is_error=True),
            modern,
        )
    return _result(request_id, _tool_result(payload), modern)


class Session:
    """Per-process state: which era, and which legacy version was negotiated."""

    def __init__(self) -> None:
        self.legacy_version: Optional[str] = None

    @property
    def initialized(self) -> bool:
        return self.legacy_version is not None


def handle_request(
    request: Dict[str, Any], session: Optional["Session"] = None
) -> Optional[Dict[str, Any]]:
    """Dispatch one JSON-RPC request. Returns ``None`` for notifications."""
    session = session if session is not None else Session()
    if not isinstance(request, dict):
        return _error(None, JSONRPC_INVALID_REQUEST, "Request must be an object.")
    if "id" in request and (isinstance(request["id"], bool) or not isinstance(request["id"], (str, int))):
        return _error(None, JSONRPC_INVALID_REQUEST, "id must be a string or integer.")
    if request.get("jsonrpc") != "2.0":
        return _error(request.get("id"), JSONRPC_INVALID_REQUEST, "jsonrpc must be '2.0'.")
    method = request.get("method")
    request_id = request.get("id")
    params = request.get("params", {})
    if not isinstance(params, dict):
        return _error(request_id, JSONRPC_INVALID_PARAMS, "params must be an object.")
    if not isinstance(method, str) or not method:
        return _error(request_id, JSONRPC_INVALID_REQUEST, "method must be a non-empty string.")

    # ---------------------------------------------------------- notifications
    if "id" not in request:
        # Notifications never execute request handlers or change handshake state.
        return None

    # ------------------------------------------------------ legacy handshake
    if method == "initialize":
        requested = params.get("protocolVersion")
        if not _valid_version(requested):
            return _error(
                request_id,
                JSONRPC_INVALID_PARAMS,
                "protocolVersion must be a string. This server supports "
                f"{', '.join(SUPPORTED_PROTOCOL_VERSIONS)}.",
                {"supported": list(SUPPORTED_PROTOCOL_VERSIONS)},
            )
        if requested in MODERN_PROTOCOL_VERSIONS:
            # A modern revision has no handshake; say so instead of pretending.
            return _error(
                request_id,
                JSONRPC_INVALID_PARAMS,
                f"Protocol version {requested} has no initialize handshake. Send requests "
                "directly with the version in params._meta, optionally probing with "
                "server/discover first.",
                {"supported": list(SUPPORTED_PROTOCOL_VERSIONS)},
            )
        if not isinstance(params.get("capabilities"), dict) or not _valid_implementation(params.get("clientInfo")):
            return _error(request_id, JSONRPC_INVALID_PARAMS, "initialize requires capabilities and clientInfo objects with name and version.")
        # The handshake exists to counter-offer: an unknown version gets the
        # newest legacy revision this server implements.
        negotiated = requested if requested in LEGACY_PROTOCOL_VERSIONS else LATEST_LEGACY_VERSION
        session.legacy_version = negotiated
        client_info = params.get("clientInfo")
        if isinstance(client_info, dict) and client_info.get("name"):
            log(f"legacy client {client_info.get('name')} {client_info.get('version', '')}".strip())
        return _result(
            request_id,
            {
                "protocolVersion": negotiated,
                "capabilities": dict(SERVER_CAPABILITIES),
                "serverInfo": {"name": SERVER_NAME, "version": ENGINE_VERSION},
                "instructions": INSTRUCTIONS,
            },
            modern=False,
        )

    # ------------------------------------------------------------ modern era
    meta = params.get("_meta")
    # A modern field is never allowed to silently fall back to legacy.
    modern = method == "server/discover" or not session.initialized or (
        "_meta" in params and (not isinstance(meta, dict) or any(
            k in meta for k in (META_PROTOCOL_VERSION, META_CLIENT_CAPABILITIES, META_CLIENT_INFO)
        ))
    )
    if modern:
        if not isinstance(meta, dict) or not _valid_version(meta.get(META_PROTOCOL_VERSION)):
            return _error(request_id, JSONRPC_INVALID_PARAMS, "Modern requests require a string protocolVersion in _meta.")
        if not isinstance(meta.get(META_CLIENT_CAPABILITIES), dict):
            return _error(request_id, JSONRPC_INVALID_PARAMS, "Modern requests require a clientCapabilities object in _meta.")
        if META_CLIENT_INFO in meta and not _valid_implementation(meta[META_CLIENT_INFO]):
            return _error(request_id, JSONRPC_INVALID_PARAMS, "clientInfo requires name and version strings.")
        requested_version = meta[META_PROTOCOL_VERSION]
        if requested_version not in MODERN_PROTOCOL_VERSIONS:
            return _unsupported_version_error(request_id, requested_version)

    if method == "server/discover":
        return _result(request_id, _discover_result(), modern=True)

    if method == "ping":
        return _result(request_id, {}, modern)

    if method == "tools/list":
        listing: Dict[str, Any] = {"tools": TOOLS}
        if modern:
            # Cacheable results of the modern era must carry caching hints.
            listing["ttlMs"] = TOOLS_LIST_TTL_MS
            listing["cacheScope"] = CACHE_SCOPE
        return _result(request_id, listing, modern)

    if method == "tools/call":
        return _handle_tools_call(request_id, params, modern)

    if method == "shutdown":
        return _result(request_id, {}, modern)

    return _error(request_id, JSONRPC_METHOD_NOT_FOUND, f"Unknown method {method!r}.")


def serve(stdin=None, stdout=None) -> None:
    stdin = stdin or sys.stdin
    stdout = stdout or sys.stdout
    session = Session()
    for line in stdin:
        line = line.strip()
        if not line:
            continue
        try:
            request = json.loads(line)
        except json.JSONDecodeError:
            response = _error(None, JSONRPC_PARSE_ERROR, "Request is not valid JSON.")
        else:
            if not isinstance(request, dict):
                response = _error(None, JSONRPC_INVALID_REQUEST, "Request must be an object.")
            else:
                response = handle_request(request, session)
        if response is None:
            continue
        stdout.write(json.dumps(response, ensure_ascii=False) + "\n")
        stdout.flush()


if __name__ == "__main__":
    serve()
