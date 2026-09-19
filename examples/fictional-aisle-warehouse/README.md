# Fictional aisle warehouse

A second, independent fictional dataset. It is **not** the historical
regression case (`fictional-small-warehouse`, 15/15 orders, 1560 m, median
100 m, p90 120 m, SWAP-001 −80 m). Do not mix those figures with this layout.

The layout is an original aisle graph inspired by common warehouse organisation
(receiving, pick aisles, packing, shipping). It is not a tracing of a
commercial floor-plan image. The engine does not import plan images; every
distance is a declared graph length.

## What is calculated vs decorative

| From the engine | Presentation overlay only |
| --- | --- |
| Aisle graph, declared `distance_m` | Rack rectangles |
| Shortest permitted path per pick sequence | Outer walls |
| Order distances and the drawn polylines | Dock doors, office, zone labels |
| Start node `RECV`, end node `PACK` | |

Routes follow aisle centre-lines and the north/south cross-aisles. They do not
cross racks. Sequence basis is `planned`; distances are modelled, not measured.

## Generate

From the repository root, after `pip install -e .`:

```bash
python examples/fictional-aisle-warehouse/build_example.py
```

That writes `dataset.json`, `spaghetti-landing.svg` and `landing-metrics.json`.

The same routes can be produced with the CLI:

```bash
ifa validate --dataset examples/fictional-aisle-warehouse/dataset.json
ifa analyze --dataset examples/fictional-aisle-warehouse/dataset.json \
  --output build/aisle-warehouse --spaghetti-top 8
```

`build/aisle-warehouse/spaghetti.svg` is the engine drawing without the
architectural overlay. `spaghetti-landing.svg` adds the overlay on the same
coordinates.

## Figures from this dataset

Recorded in `landing-metrics.json` after a successful engine run. The landing
page must use those numbers, never the historical 1560 m / 100 m / 120 m set.
