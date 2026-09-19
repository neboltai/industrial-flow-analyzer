"""Small, hand-written fixtures shared by the unit tests.

Every fixture is fictional and intentionally tiny so that each expected number
can be verified by hand from the module docstrings.
"""

from __future__ import annotations

import copy
import sys
from pathlib import Path
from typing import Any, Dict, List

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from intralogistics_flow_analyzer.models import Dataset  # noqa: E402

EXAMPLE_DATASET_PATH = ROOT / "examples" / "fictional-small-warehouse" / "dataset.json"
EXAMPLE_RAW_DIR = ROOT / "examples" / "fictional-small-warehouse" / "raw"
EXAMPLE_MAPPING_PATH = ROOT / "examples" / "fictional-small-warehouse" / "mapping.json"
EVAL_CASES_DIR = ROOT / "tests" / "evals" / "cases"

#: Linear layout used by the routing and metric tests.
#:
#:   N0 --10-- N1 --10-- N2
#:              |
#:             10
#:              |
#:             N3
#:
#: L1 hangs off N1 (10 m from the start), L2 off N2 (20 m), L3 off N3 (20 m).
MINIMAL: Dict[str, Any] = {
    "schema_version": "1.0",
    "meta": {
        "dataset_id": "unit-test-minimal",
        "site_id": "SITE-TEST",
        "period_start": "2026-01-01T00:00:00+01:00",
        "period_end": "2026-01-10T23:59:59+01:00",
        "distance_unit": "m",
        "time_zone": "Europe/Berlin",
        "source": "unit test fixture",
        "fictional": True,
    },
    "layout": {
        "nodes": [
            {"node_id": "N0", "x_m": 0.0, "y_m": 0.0, "node_type": "depot", "zone_id": "Z0"},
            {"node_id": "N1", "x_m": 10.0, "y_m": 0.0, "node_type": "location_access", "zone_id": "Z1"},
            {"node_id": "N2", "x_m": 20.0, "y_m": 0.0, "node_type": "location_access", "zone_id": "Z1"},
            {"node_id": "N3", "x_m": 10.0, "y_m": 10.0, "node_type": "location_access", "zone_id": "Z2"},
        ],
        "edges": [
            {
                "edge_id": "E1",
                "from_node": "N0",
                "to_node": "N1",
                "distance_m": 10.0,
                "bidirectional": True,
                "allowed_modes": ["pedestrian", "forklift"],
            },
            {
                "edge_id": "E2",
                "from_node": "N1",
                "to_node": "N2",
                "distance_m": 10.0,
                "bidirectional": True,
                "allowed_modes": ["pedestrian", "forklift"],
            },
            {
                "edge_id": "E3",
                "from_node": "N1",
                "to_node": "N3",
                "distance_m": 10.0,
                "bidirectional": True,
                "allowed_modes": ["pedestrian"],
            },
        ],
    },
    "locations": [
        {
            "location_id": "L1",
            "access_node_id": "N1",
            "zone_id": "Z1",
            "location_type": "pick",
            "capacity_volume_m3": 1.0,
            "capacity_weight_kg": 200.0,
            "max_unit_weight_kg": 20.0,
            "temperature_zone": "ambient",
            "hazard_classes_allowed": ["none"],
            "allowed_modes": ["pedestrian", "forklift"],
            "level": 1,
            "fixed": False,
        },
        {
            "location_id": "L2",
            "access_node_id": "N2",
            "zone_id": "Z1",
            "location_type": "pick",
            "capacity_volume_m3": 1.0,
            "capacity_weight_kg": 200.0,
            "max_unit_weight_kg": 20.0,
            "temperature_zone": "ambient",
            "hazard_classes_allowed": ["none"],
            "allowed_modes": ["pedestrian", "forklift"],
            "level": 1,
            "fixed": False,
        },
        {
            "location_id": "L3",
            "access_node_id": "N3",
            "zone_id": "Z2",
            "location_type": "pick",
            "capacity_volume_m3": 1.0,
            "capacity_weight_kg": 200.0,
            "max_unit_weight_kg": 20.0,
            "temperature_zone": "ambient",
            "hazard_classes_allowed": ["none"],
            "allowed_modes": ["pedestrian"],
            "level": 1,
            "fixed": False,
        },
    ],
    "items": [
        {
            "sku": "S1",
            "description": "Test item one",
            "unit_volume_m3": 0.001,
            "unit_weight_kg": 1.0,
            "temperature_zone": "ambient",
            "hazard_class": "none",
            "handling_mode": "pedestrian",
            "fixed_location": False,
        },
        {
            "sku": "S2",
            "description": "Test item two",
            "unit_volume_m3": 0.001,
            "unit_weight_kg": 1.0,
            "temperature_zone": "ambient",
            "hazard_class": "none",
            "handling_mode": "pedestrian",
            "fixed_location": False,
        },
        {
            "sku": "S3",
            "description": "Test item three",
            "unit_volume_m3": 0.001,
            "unit_weight_kg": 1.0,
            "temperature_zone": "ambient",
            "hazard_class": "none",
            "handling_mode": "pedestrian",
            "fixed_location": False,
        },
    ],
    "assignments": [
        {"sku": "S1", "location_id": "L2", "role": "pick", "current_units": 10, "max_units": 50},
        {"sku": "S2", "location_id": "L1", "role": "pick", "current_units": 10, "max_units": 50},
        {"sku": "S3", "location_id": "L3", "role": "pick", "current_units": 10, "max_units": 50},
    ],
    "picks": [
        {
            "pick_id": "P1",
            "order_id": "O1",
            "pick_sequence": 1,
            "timestamp": "2026-01-01T08:00:00+01:00",
            "sku": "S1",
            "location_id": "L2",
            "quantity": 1,
            "mode": "pedestrian",
        },
        {
            "pick_id": "P2",
            "order_id": "O1",
            "pick_sequence": 2,
            "timestamp": "2026-01-01T08:05:00+01:00",
            "sku": "S2",
            "location_id": "L1",
            "quantity": 2,
            "mode": "pedestrian",
        },
        {
            "pick_id": "P3",
            "order_id": "O2",
            "pick_sequence": 1,
            "timestamp": "2026-01-02T08:00:00+01:00",
            "sku": "S1",
            "location_id": "L2",
            "quantity": 3,
            "mode": "pedestrian",
        },
        {
            "pick_id": "P4",
            "order_id": "O3",
            "pick_sequence": 1,
            "timestamp": "2026-01-03T08:00:00+01:00",
            "sku": "S3",
            "location_id": "L3",
            "quantity": 1,
            "mode": "pedestrian",
        },
    ],
    "config": {
        "route_start_node": "N0",
        "route_end_node": "N0",
        "default_mode": "pedestrian",
        "abc_basis": "pick_lines",
        "abc_thresholds": {"A": 0.8, "B": 0.95},
        "xyz_time_bucket": "day",
        "xyz_minimum_buckets": 7,
        "xyz_thresholds": {"X": 0.5, "Y": 1.0},
        "minimum_improvement_percent": 1.0,
        "maximum_recommendations": 10,
        "maximum_candidate_swaps": 500,
        "affinity_minimum_co_picks": 1,
        "affinity_minimum_support": 0.0,
        "restrict_swaps_to_same_zone": False,
        "allow_level_change": False,
    },
}


def minimal_document(**overrides: Any) -> Dict[str, Any]:
    document = copy.deepcopy(MINIMAL)
    document.update(copy.deepcopy(overrides))
    return document


def minimal_dataset(**overrides: Any) -> Dataset:
    return Dataset.from_dict(minimal_document(**overrides))


def mutate(document: Dict[str, Any], path: List[Any], value: Any) -> Dict[str, Any]:
    """Set ``document[path...] = value`` on a deep copy and return it."""
    result = copy.deepcopy(document)
    cursor: Any = result
    for key in path[:-1]:
        cursor = cursor[key]
    cursor[path[-1]] = value
    return result


def example_dataset() -> Dataset:
    from intralogistics_flow_analyzer.loaders import load_dataset

    return load_dataset(EXAMPLE_DATASET_PATH)
