from __future__ import annotations

import hashlib
import json
import unittest
from pathlib import Path

from intralogistics_flow_analyzer.runtime_contract import (
    RuntimeContractError,
    analyze_request,
    health_document,
    validate_request,
)

ROOT=Path(__file__).resolve().parents[1]


class RuntimeContractTest(unittest.TestCase):
    def request(self) -> dict:
        path=ROOT/"examples"/"fictional-small-warehouse"/"dataset.json"
        raw=path.read_bytes()
        return {
            "contract_version":"1.0",
            "platform_run_id":"01TESTFLOW00000000000000",
            "product_id":"flow",
            "product_version":"0.1.2",
            "input_fingerprint":hashlib.sha256(raw).hexdigest(),
            "configuration_fingerprint":hashlib.sha256(b"{}").hexdigest(),
            "dataset":json.loads(raw),
            "configuration":{"top_recommendations":3},
        }

    def test_health(self):
        health=health_document()
        self.assertEqual("ready",health["status"])
        self.assertEqual("flow",health["product_id"])
        self.assertEqual("0.1.2",health["version"])

    def test_dataset_validation(self):
        request=self.request()
        request.pop("platform_run_id")
        result=validate_request(request)
        self.assertIn(result["status"],{"valid","partial","invalid"})
        self.assertEqual("flow",result["product_id"])

    def test_analysis_is_normalized(self):
        result=analyze_request(self.request())
        self.assertEqual("flow",result["product_id"])
        self.assertEqual("0.1.2",result["product_version"])
        self.assertIn(result["status"],{"completed","partial","blocked"})
        self.assertTrue(result["evidence"])
        self.assertIn("metrics",result["summary"])
        for finding in result["findings"]:
            self.assertIn(finding["claim_level"],{"observation","finding","hypothesis"})

    def test_wrong_version_fails_closed(self):
        request=self.request()
        request["product_version"]="0.0.0"
        with self.assertRaises(RuntimeContractError):
            analyze_request(request)


if __name__=="__main__":
    unittest.main()
