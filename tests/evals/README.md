# Evaluation cases

Deterministic regression cases for the analysis engine. They pin behaviour that
must not drift: the status a dataset produces, the issue codes it raises, the
metrics it computes, the swaps it may and may not propose, and the claims it is
allowed to make.

Run them with:

```bash
ifa run-evals
# or
python -m intralogistics_flow_analyzer run-evals --cases-dir tests/evals/cases
```

The runner prints one line per category and an overall line:

```
validation
routing
metrics
classification
constraint handling
unsupported claims
overall
```

Exit code 0 means every case passed.

## Case format

One JSON file per case in `cases/`.

| Key | Meaning |
| --- | --- |
| `case_id` | Stable identifier. |
| `category` | One of the six categories above. |
| `description` | What the case pins, in one sentence. |
| `dataset` | Inline canonical dataset. |
| `dataset_path` | Path to a dataset file, relative to the case file. Use instead of `dataset`. |
| `expected_status` | `ok`, `partial`, `insufficient_evidence` or `data_error`. |
| `expected_exit_code` | 0, 1 or 2. |
| `required_issue_codes` | Codes that must appear. |
| `forbidden_issue_codes` | Codes that must not appear. |
| `expected_metrics` | `{"metric": {"value": x, "tolerance": t}}`; a bare number means tolerance 0. |
| `expected_abc_classes` | `{"SKU": "A"}`. |
| `expected_xyz_marker` | `XYZ_NOT_COMPUTED_INSUFFICIENT_PERIODS` or `null`. |
| `expected_affinity` | `{"pair": "A\|B", "co_pick_count": n}`. |
| `allowed_recommendations` | Whitelist of `"SKU-A\|SKU-B"` pairs; anything else fails the case. |
| `forbidden_recommendations` | Pairs that must never be recommended. |
| `expected_recommendation_count` | Exact number of accepted swaps. |
| `expected_rejected_reasons` | `{"A\|B": "temperature"}` — the check or status that must block the pair. |
| `expected_claim_types` | Claim types that must appear among the findings. |
| `forbidden_claim_types` | Claim types that must not appear. |
| `forbidden_report_substrings` | Wording that must never appear anywhere in `analysis.json`. |

Every numeric expectation carries an explicit tolerance, so a case states how
precise it intends to be instead of relying on floating-point luck.

## What the shipped cases cover

| Case | Pins |
| --- | --- |
| `01-example-warehouse-baseline` | the reference metrics of the fictional warehouse |
| `02-validation-duplicate-pick-id` | a duplicated identifier is blocking |
| `03-validation-temperature-mismatch` | a master-data breach is reported, not repaired |
| `04-routing-missing-sequence` | no `pick_sequence`, no invented distance |
| `05-routing-unreachable-segment` | an unreachable order is excluded and named |
| `06-metrics-minimal-layout` | hand-checkable reference numbers |
| `07-classification-xyz-refused` | too few buckets: XYZ is not computed |
| `08-classification-abc-thresholds` | ABC classes on a skewed profile |
| `09-constraints-fixed-location` | a pinned location is never proposed |
| `10-constraints-missing-data` | an unknown constraint blocks the move |
| `11-constraints-unit-weight` | a too-heavy unit blocks the move |
| `12-unsupported-claims` | no gain: a hypothesis, never a simulated figure |
| `13-unsupported-claims-example` | wording stays an estimate, no worker data |

## Adding a case

Keep the dataset as small as the behaviour allows, state the expectation
explicitly, and make sure the case fails if the rule it protects is removed.
