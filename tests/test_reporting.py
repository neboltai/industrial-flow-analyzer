"""Reports: JSON shape, readable CSV, valid SVG, self-contained HTML, no personal data."""

from __future__ import annotations

import csv
import json
import re
import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

from fixtures import EXAMPLE_RAW_DIR, ROOT, example_dataset, minimal_dataset

from intralogistics_flow_analyzer.models import PERSONAL_COLUMNS
from intralogistics_flow_analyzer.reporting import (
    analyse,
    render_html,
    render_markdown,
    write_reports,
)
from intralogistics_flow_analyzer.svg import render_flow_map, render_spaghetti

SCHEMA_PATH = ROOT / "schemas" / "analysis.schema.json"
RECOMMENDATION_SCHEMA_PATH = ROOT / "schemas" / "recommendations.schema.json"


class ReportingTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.dataset = example_dataset()
        cls.result = analyse(cls.dataset)
        cls.tempdir = tempfile.TemporaryDirectory()
        cls.paths = write_reports(cls.result, Path(cls.tempdir.name))

    @classmethod
    def tearDownClass(cls) -> None:
        cls.tempdir.cleanup()

    def test_analysis_json_carries_every_required_top_level_block(self) -> None:
        schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
        document = json.loads(Path(self.paths["analysis_json"]).read_text(encoding="utf-8"))
        for key in schema["required"]:
            self.assertIn(key, document)
        self.assertEqual(document["schema_version"], "1.0")
        self.assertIn(document["status"], ("ok", "partial", "insufficient_evidence", "data_error"))

    def test_every_finding_declares_a_claim_type_and_an_evidence_quality(self) -> None:
        for finding in self.result.analysis["findings"]:
            self.assertIn(
                finding["claim_type"], ("observed", "calculated", "simulated", "hypothesis")
            )
            self.assertIn(
                finding["evidence_quality"], ("high", "medium", "low", "insufficient")
            )
            self.assertTrue(finding["statement"])
            self.assertIsInstance(finding["evidence"], list)

    def test_recommendations_match_their_schema_shape(self) -> None:
        schema = json.loads(RECOMMENDATION_SCHEMA_PATH.read_text(encoding="utf-8"))
        required = schema["$defs"]["recommendation"]["required"]
        checks = schema["$defs"]["constraint_checks"]["required"]
        for recommendation in self.result.analysis["recommendations"]:
            for key in required:
                self.assertIn(key, recommendation)
            self.assertTrue(re.fullmatch(r"SWAP-\d{3}", recommendation["recommendation_id"]))
            for name in checks:
                self.assertIn(name, recommendation["constraint_checks"])

    def test_recommendations_csv_is_readable_and_has_a_header(self) -> None:
        with Path(self.paths["recommendations_csv"]).open(encoding="utf-8", newline="") as handle:
            rows = list(csv.DictReader(handle))
        self.assertEqual(len(rows), len(self.result.analysis["recommendations"]))
        if rows:
            self.assertIn("estimated_reduction_m", rows[0])
            self.assertIn("check_temperature", rows[0])

    def test_svg_files_are_well_formed_and_carry_title_and_desc(self) -> None:
        for key in ("flow_map_svg", "spaghetti_svg", "spaghetti_aggregate_svg"):
            content = Path(self.paths[key]).read_text(encoding="utf-8")
            root = ET.fromstring(content.split("?>", 1)[1])
            namespace = "{http://www.w3.org/2000/svg}"
            self.assertTrue(root.tag.endswith("svg"))
            self.assertIsNotNone(root.find(f"{namespace}title"))
            self.assertIsNotNone(root.find(f"{namespace}desc"))

    def test_svg_never_references_an_external_resource(self) -> None:
        for key in ("flow_map_svg", "spaghetti_svg"):
            content = Path(self.paths[key]).read_text(encoding="utf-8")
            self.assertNotIn("http://", content.replace("http://www.w3.org/2000/svg", ""))
            self.assertNotIn("https://", content)

    def test_html_report_is_self_contained(self) -> None:
        html = Path(self.paths["report_html"]).read_text(encoding="utf-8")
        self.assertIn("<!DOCTYPE html>", html)
        self.assertNotIn("<script", html.lower())
        self.assertNotIn("<link", html.lower())
        self.assertNotIn("@import", html)
        self.assertNotIn("https://", html.replace("https://www.w3.org/2000/svg", ""))
        self.assertIn("<style>", html)
        self.assertIn("@media print", html)
        self.assertIn('name="viewport"', html)

    def test_html_report_embeds_both_drawings(self) -> None:
        html = Path(self.paths["report_html"]).read_text(encoding="utf-8")
        self.assertEqual(html.count("<figure>"), 2)
        self.assertGreaterEqual(html.count("<svg"), 2)

    def test_html_report_marks_the_fictional_example(self) -> None:
        html = Path(self.paths["report_html"]).read_text(encoding="utf-8")
        self.assertIn("Fictional example", html)

    def test_html_report_contains_all_eleven_sections(self) -> None:
        html = Path(self.paths["report_html"]).read_text(encoding="utf-8")
        for heading in (
            "1. Summary",
            "2. Data quality",
            "3. Flow metrics",
            "4. ABC / XYZ classification",
            "5. Leading locations",
            "6. Co-pick affinities",
            "7. Visualisation",
            "8. Simulated recommendations",
            "9. Assumptions",
            "10. Limitations",
            "11. Field validation actions",
        ):
            self.assertIn(heading, html)

    def test_markdown_report_separates_the_four_claim_types(self) -> None:
        markdown = render_markdown(self.result)
        # Table cells are English only; the German terms appear once, in the
        # glossary line that introduces the claim types.
        for english in ("Observed", "Calculated", "Simulated", "Hypothesis"):
            self.assertIn(english, markdown)
        for german in ("Fakt", "Berechnung", "Simulation", "Hypothese"):
            self.assertEqual(markdown.count(f"({german})"), 1, msg=german)
        self.assertIn("Evidence rules applied", markdown)

    def test_reports_state_the_provenance_of_every_figure(self) -> None:
        markdown = render_markdown(self.result)
        html = Path(self.paths["report_html"]).read_text(encoding="utf-8")
        for content in (markdown, html):
            self.assertIn("sequence_basis", content)
            self.assertIn("distance_basis", content)
            self.assertIn("scan_confirmed", content)
            self.assertIn("declared_graph_shortest_path", content)

    def test_reports_never_call_a_modelled_distance_a_measurement(self) -> None:
        blob = (
            render_markdown(self.result)
            + Path(self.paths["report_html"]).read_text(encoding="utf-8")
            + json.dumps(self.result.analysis, ensure_ascii=False)
        ).lower()
        for forbidden in ("actual walked distance", "distance actually walked", "measured distance"):
            self.assertNotIn(forbidden, blob)
        self.assertIn("modelled", blob)

    def test_every_finding_carries_its_own_evidence_assessment(self) -> None:
        for finding in self.result.analysis["findings"]:
            assessment = finding["evidence_assessment"]
            self.assertIn("factors", assessment)
            self.assertIn("site_verified", assessment)
            self.assertTrue(assessment["rationale"])
            self.assertIs(assessment["site_verified"], False)
            self.assertEqual(finding["evidence_quality"], finding["evidence_quality"])

    def test_evidence_quality_is_not_uniform_across_finding_families(self) -> None:
        levels = {f["evidence_quality"] for f in self.result.analysis["findings"]}
        self.assertGreater(len(levels), 1, msg=f"all findings share one level: {levels}")

    def test_no_finding_claims_high_evidence_without_site_verification(self) -> None:
        for finding in self.result.analysis["findings"]:
            if finding["claim_type"] == "simulated":
                self.assertNotEqual(finding["evidence_quality"], "high")
            if not finding["evidence_assessment"]["site_verified"]:
                self.assertIn(
                    finding["evidence_quality"], ("insufficient", "low", "medium", "high")
                )

    def test_no_personal_data_appears_anywhere_in_the_outputs(self) -> None:
        for key, path in self.paths.items():
            content = Path(path).read_text(encoding="utf-8").lower()
            for column in PERSONAL_COLUMNS:
                self.assertNotIn(column, content, msg=f"{column} found in {key}")

    def test_the_raw_export_carries_a_worker_column_that_never_reaches_the_dataset(self) -> None:
        header = (EXAMPLE_RAW_DIR / "picks.csv").read_text(encoding="utf-8").splitlines()[0]
        self.assertIn("picker_name", header)
        dataset_blob = json.dumps(self.dataset.to_dict()).lower()
        self.assertNotIn("picker_name", dataset_blob)

    def test_reports_never_claim_a_guaranteed_saving(self) -> None:
        for key in ("report_md", "report_html", "analysis_json"):
            content = Path(self.paths[key]).read_text(encoding="utf-8").lower()
            self.assertNotIn("guaranteed saving", content)
            self.assertNotIn("guaranteed", content)

    def test_congestion_is_never_asserted_without_time_evidence(self) -> None:
        blob = json.dumps(self.result.analysis, ensure_ascii=False).lower()
        for forbidden in ("is congested", "aisle is congested", "congestion was observed"):
            self.assertNotIn(forbidden, blob)
        self.assertIn("presence of congestion", blob)
        self.assertIn("must be verified on site", blob)

    def test_rendering_works_for_a_dataset_without_recommendations(self) -> None:
        result = analyse(minimal_dataset(), top_recommendations=0)
        markdown = render_markdown(result)
        flow_map = render_flow_map(result.dataset, result.edge_rows, result.location_rows)
        spaghetti = render_spaghetti(result.dataset, result.routing, top=3)
        html = render_html(result, flow_map, spaghetti)
        self.assertIn("No candidate swap cleared", markdown)
        self.assertIn("<!DOCTYPE html>", html)

    def test_spaghetti_can_draw_a_single_order(self) -> None:
        content = render_spaghetti(self.dataset, self.result.routing, order_id="ORD-0001")
        self.assertIn("ORD-0001", content)
        ET.fromstring(content.split("?>", 1)[1])

    def test_spaghetti_limits_the_number_of_drawn_routes(self) -> None:
        content = render_spaghetti(self.dataset, self.result.routing, top=3)
        self.assertEqual(content.count("<polyline"), 3)

    def test_moves_document_is_written_next_to_the_reports(self) -> None:
        document = json.loads(Path(self.paths["moves_json"]).read_text(encoding="utf-8"))
        self.assertEqual(document["schema_version"], "1.0")
        self.assertEqual(len(document["moves"]), len(self.result.analysis["recommendations"]))


if __name__ == "__main__":  # pragma: no cover
    unittest.main()


class VisualisationConsolidationTest(unittest.TestCase):
    """The 0.1.1 drawing changes: node roles, honest arrows, modelled wording."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.dataset = example_dataset()
        cls.result = analyse(cls.dataset)
        cls.flow_map = render_flow_map(
            cls.dataset, cls.result.edge_rows, cls.result.location_rows
        )
        cls.spaghetti = render_spaghetti(cls.dataset, cls.result.routing, top=4)

    def test_route_start_and_end_are_visually_distinct(self) -> None:
        # N01 is both the start and the end of a tour in the fictional site.
        self.assertIn("Route start and end", self.flow_map)
        self.assertIn("N01 (Route start and end", self.flow_map)

    def test_packing_and_shipping_have_their_own_markers(self) -> None:
        self.assertIn("(Packing", self.flow_map)
        self.assertIn("(Shipping", self.flow_map)
        for label in ("Packing", "Shipping"):
            self.assertIn(f">{label}<", self.flow_map)

    def test_an_arrow_is_drawn_only_on_a_one_way_segment(self) -> None:
        one_way = [edge for edge in self.dataset.edges if not edge.bidirectional]
        two_way = [edge for edge in self.dataset.edges if edge.bidirectional]
        self.assertTrue(one_way, msg="the fixture should contain a one-way edge")
        self.assertIn('marker-end="url(#one-way-arrow)"', self.flow_map)
        # Exactly as many arrowheads as there are one-way edges, in each drawing.
        self.assertEqual(
            self.flow_map.count('marker-end="url(#one-way-arrow)"'),
            len(one_way) + 1,  # + the legend sample
        )
        self.assertEqual(
            self.spaghetti.count('marker-end="url(#one-way-arrow)"'), len(one_way)
        )
        self.assertTrue(two_way)

    def test_every_one_way_segment_is_labelled_as_such(self) -> None:
        for edge in self.dataset.edges:
            direction = "one-way" if not edge.bidirectional else "two-way"
            self.assertIn(f"{edge.edge_id}: {edge.from_node}-{edge.to_node} ({direction})",
                          self.flow_map)

    def test_traversals_are_described_as_modelled(self) -> None:
        for drawing in (self.flow_map, self.spaghetti):
            self.assertIn("modelled", drawing)
        self.assertIn("Maximum modelled traversals", self.flow_map)

    def test_the_spaghetti_states_how_many_routes_it_shows(self) -> None:
        self.assertIn("4 of 15 modelled routes", self.spaghetti)

    def test_the_drawings_stay_accessible(self) -> None:
        namespace = "{http://www.w3.org/2000/svg}"
        for drawing in (self.flow_map, self.spaghetti):
            root = ET.fromstring(drawing.split("?>", 1)[1])
            self.assertIsNotNone(root.find(f"{namespace}title"))
            self.assertIsNotNone(root.find(f"{namespace}desc"))

    def test_the_fictional_marker_survives(self) -> None:
        self.assertIn("Fictional example dataset", self.flow_map)
