"""Constraint gate: an unknown constraint blocks a move, it never passes it."""

from __future__ import annotations

import unittest

from fixtures import minimal_document, mutate

from intralogistics_flow_analyzer.constraints import (
    CHECK_ORDER,
    FAIL,
    INSUFFICIENT,
    PASS,
    STATUS_BLOCKED,
    STATUS_INSUFFICIENT,
    STATUS_OK,
    check_pair_swap,
)
from intralogistics_flow_analyzer.models import Dataset


def result_for(document: dict, sku_a: str = "S1", sku_b: str = "S2"):
    return check_pair_swap(Dataset.from_dict(document), sku_a, sku_b)


class ConstraintTest(unittest.TestCase):
    def test_compatible_pair_passes_every_check(self) -> None:
        result = result_for(minimal_document())
        self.assertEqual(result.status, STATUS_OK)
        self.assertTrue(result.allowed)
        for name in CHECK_ORDER:
            self.assertEqual(result.checks[name], PASS, msg=name)

    def test_all_nine_mandatory_checks_are_always_present(self) -> None:
        result = result_for(minimal_document())
        self.assertEqual(sorted(result.to_dict()), sorted(CHECK_ORDER))
        self.assertEqual(len(CHECK_ORDER), 9)

    def test_unit_weight_blocks_a_heavy_item(self) -> None:
        document = mutate(minimal_document(), ["items", 0, "unit_weight_kg"], 30.0)
        document = mutate(document, ["locations", 1, "max_unit_weight_kg"], 50.0)
        document = mutate(document, ["locations", 1, "capacity_weight_kg"], 5000.0)
        result = result_for(document)
        self.assertEqual(result.checks["unit_weight"], FAIL)
        self.assertEqual(result.status, STATUS_BLOCKED)
        self.assertIn("unit_weight", result.failed_checks())

    def test_total_weight_blocks_an_overloaded_location(self) -> None:
        document = mutate(minimal_document(), ["assignments", 0, "max_units"], 1000)
        result = result_for(document)
        self.assertEqual(result.checks["weight"], FAIL)

    def test_volume_blocks_an_oversized_item(self) -> None:
        document = mutate(minimal_document(), ["items", 0, "unit_volume_m3"], 1.0)
        document = mutate(document, ["locations", 1, "max_unit_weight_kg"], 20.0)
        result = result_for(document)
        self.assertEqual(result.checks["volume"], FAIL)

    def test_temperature_mismatch_blocks_the_swap(self) -> None:
        document = mutate(minimal_document(), ["locations", 0, "temperature_zone"], "chilled")
        result = result_for(document)
        self.assertEqual(result.checks["temperature"], FAIL)

    def test_hazard_class_not_allowed_blocks_the_swap(self) -> None:
        document = mutate(minimal_document(), ["items", 0, "hazard_class"], "flammable")
        document = mutate(document, ["locations", 1, "hazard_classes_allowed"], ["none", "flammable"])
        result = result_for(document)
        self.assertEqual(result.checks["hazard"], FAIL)

    def test_handling_mode_not_allowed_blocks_the_swap(self) -> None:
        document = mutate(minimal_document(), ["items", 1, "handling_mode"], "forklift")
        document = mutate(document, ["assignments", 1, "location_id"], "L1")
        document = mutate(document, ["assignments", 0, "location_id"], "L3")
        result = result_for(document)
        self.assertEqual(result.checks["handling_mode"], FAIL)

    def test_fixed_location_blocks_the_swap(self) -> None:
        document = mutate(minimal_document(), ["locations", 1, "fixed"], True)
        result = result_for(document)
        self.assertEqual(result.checks["fixed"], FAIL)

    def test_pinned_item_blocks_the_swap(self) -> None:
        document = mutate(minimal_document(), ["items", 0, "fixed_location"], True)
        result = result_for(document)
        self.assertEqual(result.checks["fixed"], FAIL)

    def test_level_change_is_refused_unless_configured(self) -> None:
        document = mutate(minimal_document(), ["locations", 0, "level"], 3)
        self.assertEqual(result_for(document).checks["level"], FAIL)
        document["config"]["allow_level_change"] = True
        self.assertEqual(result_for(document).checks["level"], PASS)

    def test_same_zone_restriction_is_applied_when_configured(self) -> None:
        document = minimal_document()
        document["config"]["restrict_swaps_to_same_zone"] = True
        self.assertEqual(result_for(document, "S1", "S3").checks["zone"], FAIL)
        self.assertEqual(result_for(document, "S1", "S2").checks["zone"], PASS)

    def test_non_pick_location_is_refused(self) -> None:
        document = mutate(minimal_document(), ["locations", 0, "location_type"], "reserve")
        self.assertEqual(result_for(document).checks["zone"], FAIL)

    def test_missing_constraint_data_yields_insufficient_not_pass(self) -> None:
        document = mutate(minimal_document(), ["items", 0, "unit_weight_kg"], None)
        result = result_for(document)
        self.assertEqual(result.checks["weight"], INSUFFICIENT)
        self.assertEqual(result.checks["unit_weight"], INSUFFICIENT)
        self.assertEqual(result.status, STATUS_INSUFFICIENT)
        self.assertFalse(result.allowed)

    def test_missing_temperature_zone_is_insufficient_not_compatible(self) -> None:
        document = mutate(minimal_document(), ["locations", 1, "temperature_zone"], None)
        result = result_for(document)
        self.assertEqual(result.checks["temperature"], INSUFFICIENT)
        self.assertFalse(result.allowed)

    def test_unknown_sku_yields_insufficient_for_every_check(self) -> None:
        result = result_for(minimal_document(), "S1", "S_MISSING")
        self.assertEqual(result.status, STATUS_INSUFFICIENT)
        self.assertEqual(set(result.checks.values()), {INSUFFICIENT})

    def test_a_failure_outranks_an_unknown(self) -> None:
        document = mutate(minimal_document(), ["items", 0, "unit_volume_m3"], None)
        document = mutate(document, ["locations", 1, "fixed"], True)
        result = result_for(document)
        self.assertEqual(result.checks["volume"], INSUFFICIENT)
        self.assertEqual(result.checks["fixed"], FAIL)
        self.assertEqual(result.status, STATUS_BLOCKED)

    def test_every_blocking_check_carries_a_readable_reason(self) -> None:
        document = mutate(minimal_document(), ["locations", 0, "temperature_zone"], "chilled")
        result = result_for(document)
        self.assertIn("temperature", result.reasons)
        self.assertIn("chilled", result.reasons["temperature"])


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
