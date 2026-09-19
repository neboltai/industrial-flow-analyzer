"""ABC and XYZ classification.

ABC
---
Items are ranked by descending contribution on the configured basis
(``pick_lines``, ``units`` or ``orders``). The cumulative share decides the
class. The item that *crosses* a threshold belongs to the class it completes, so
the A class always reaches at least the configured share. Ties on the basis
value are broken by SKU so the result is reproducible.

XYZ
---
XYZ describes demand *variability*, not demand size. Demand is bucketed by
calendar day or ISO week, and the coefficient of variation is computed on the
per-bucket pick-line counts:

    cv = population standard deviation / mean

Buckets with no demand inside the observed period count as zero, because a day
without a pick is real information about variability.

If fewer buckets exist than ``config.xyz_minimum_buckets``, nothing is
classified. The engine returns the marker ``XYZ_NOT_COMPUTED_INSUFFICIENT_PERIODS``
instead of silently labelling everything ``Z``.
"""

from __future__ import annotations

import statistics
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

from .models import Dataset

XYZ_NOT_COMPUTED = "XYZ_NOT_COMPUTED_INSUFFICIENT_PERIODS"


def _basis_values(dataset: Dataset, basis: str) -> Dict[str, float]:
    values: Dict[str, float] = {item.sku: 0.0 for item in dataset.items}
    if basis == "pick_lines":
        for pick in dataset.picks:
            if pick.sku in values:
                values[pick.sku] += 1.0
    elif basis == "units":
        for pick in dataset.picks:
            if pick.sku in values:
                values[pick.sku] += pick.quantity
    elif basis == "orders":
        seen: Dict[str, set] = {sku: set() for sku in values}
        for pick in dataset.picks:
            if pick.sku in seen:
                seen[pick.sku].add(pick.order_id)
        for sku, orders in seen.items():
            values[sku] = float(len(orders))
    else:  # pragma: no cover - validation rejects this earlier
        raise ValueError(f"unsupported abc_basis: {basis!r}")
    return values


def abc_classify(dataset: Dataset) -> List[Dict[str, Any]]:
    """Return one row per SKU with its ABC class and cumulative share."""
    basis = str(dataset.config.get("abc_basis") or "pick_lines")
    thresholds = dataset.config.get("abc_thresholds") or {}
    a_threshold = float(thresholds.get("A", 0.8))
    b_threshold = float(thresholds.get("B", 0.95))

    values = _basis_values(dataset, basis)
    total = sum(values.values())
    ordered = sorted(values.items(), key=lambda kv: (-kv[1], kv[0]))

    rows: List[Dict[str, Any]] = []
    cumulative = 0.0
    for rank, (sku, value) in enumerate(ordered, start=1):
        share = (value / total) if total else 0.0
        previous_cumulative = cumulative
        cumulative += share
        if total == 0 or value == 0:
            abc_class = "C"
        elif previous_cumulative < a_threshold - 1e-12:
            abc_class = "A"
        elif previous_cumulative < b_threshold - 1e-12:
            abc_class = "B"
        else:
            abc_class = "C"
        rows.append(
            {
                "sku": sku,
                "rank": rank,
                "basis": basis,
                "basis_value": round(value, 6),
                "share_percent": round(100.0 * share, 4),
                "cumulative_share_percent": round(100.0 * cumulative, 4),
                "abc_class": abc_class,
            }
        )
    return rows


def _bucket_key(stamp: datetime, bucket: str) -> str:
    if bucket == "day":
        return stamp.date().isoformat()
    if bucket == "week":
        iso = stamp.isocalendar()
        return f"{iso[0]}-W{iso[1]:02d}"
    raise ValueError(f"unsupported xyz_time_bucket: {bucket!r}")


def _parse(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _expected_buckets(dataset: Dataset, bucket: str) -> List[str]:
    """All buckets inside the declared period, so empty days count as zero."""
    start = _parse(dataset.meta.period_start)
    end = _parse(dataset.meta.period_end)
    if start is None or end is None:
        stamps = [s for s in (_parse(p.timestamp) for p in dataset.picks) if s]
        if not stamps:
            return []
        start, end = min(stamps), max(stamps)
    keys: List[str] = []
    cursor: date = start.date()
    last: date = end.date()
    step = timedelta(days=1)
    while cursor <= last:
        key = _bucket_key(datetime(cursor.year, cursor.month, cursor.day), bucket)
        if key not in keys:
            keys.append(key)
        cursor = cursor + step
    return keys


def xyz_classify(dataset: Dataset) -> Tuple[List[Dict[str, Any]], Optional[str]]:
    """Return ``(rows, marker)``. ``marker`` is set when XYZ was not computed."""
    bucket = str(dataset.config.get("xyz_time_bucket") or "day")
    minimum = int(dataset.config.get("xyz_minimum_buckets") or 7)
    thresholds = dataset.config.get("xyz_thresholds") or {}
    x_threshold = float(thresholds.get("X", 0.5))
    y_threshold = float(thresholds.get("Y", 1.0))

    buckets = _expected_buckets(dataset, bucket)
    if len(buckets) < minimum:
        return [], XYZ_NOT_COMPUTED

    counts: Dict[str, Dict[str, float]] = {item.sku: {b: 0.0 for b in buckets} for item in dataset.items}
    unstamped = 0
    for pick in dataset.picks:
        stamp = _parse(pick.timestamp)
        if stamp is None:
            unstamped += 1
            continue
        key = _bucket_key(stamp, bucket)
        if pick.sku in counts and key in counts[pick.sku]:
            counts[pick.sku][key] += 1.0

    if unstamped == len(dataset.picks):
        return [], XYZ_NOT_COMPUTED

    rows: List[Dict[str, Any]] = []
    for sku in sorted(counts):
        series = [counts[sku][b] for b in buckets]
        mean = statistics.fmean(series)
        if mean == 0:
            rows.append(
                {
                    "sku": sku,
                    "buckets": len(buckets),
                    "bucket_type": bucket,
                    "mean_per_bucket": 0.0,
                    "stdev_per_bucket": 0.0,
                    "coefficient_of_variation": None,
                    "xyz_class": None,
                    "note": "No demand in the observed period; variability is undefined.",
                }
            )
            continue
        stdev = statistics.pstdev(series)
        cv = stdev / mean
        if cv <= x_threshold:
            xyz_class = "X"
        elif cv <= y_threshold:
            xyz_class = "Y"
        else:
            xyz_class = "Z"
        rows.append(
            {
                "sku": sku,
                "buckets": len(buckets),
                "bucket_type": bucket,
                "mean_per_bucket": round(mean, 6),
                "stdev_per_bucket": round(stdev, 6),
                "coefficient_of_variation": round(cv, 6),
                "xyz_class": xyz_class,
                "note": None,
            }
        )
    return rows, None


def combined_classes(
    abc_rows: List[Dict[str, Any]], xyz_rows: List[Dict[str, Any]]
) -> Dict[str, str]:
    xyz_by_sku = {row["sku"]: row.get("xyz_class") for row in xyz_rows}
    combined: Dict[str, str] = {}
    for row in abc_rows:
        xyz = xyz_by_sku.get(row["sku"])
        combined[row["sku"]] = f"{row['abc_class']}{xyz}" if xyz else row["abc_class"]
    return combined
