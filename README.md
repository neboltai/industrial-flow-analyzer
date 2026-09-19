# Intralogistics Flow & Slotting Analyzer — Community Edition

A local, open-source analysis plugin that turns structured warehouse data into reproducible route metrics, spaghetti diagrams and constraint-safe slotting simulations.

**Pickwege sichtbar machen. Lagerplätze datenbasiert verbessern.**
*Der Algorithmus rechnet. Die KI ordnet ein.*

---

## 1. The problem it solves

In most warehouses, nobody can say how far the picking actually walks. The WMS
knows what was picked and where from; it rarely knows what that cost in metres.
So slotting debates run on intuition: "that item feels badly placed", "that aisle
feels busy", "moving these two together would surely help".

This tool replaces the feeling with an arithmetic you can audit. It reconstructs
each historical order on the declared aisle graph, counts the metres, shows which
locations and aisle segments carry the traffic, and then — under explicit
constraints — simulates what a specific two-location swap would have done to
those same orders.

The metres are **modelled**, not measured: they are the shortest permitted path
on the graph you declared, between consecutive pick access nodes, in the sequence
you supplied. Every dataset states where that sequence came from
(`sequence_basis`) and how the distance was obtained (`distance_basis`), and both
appear in every report.

## 2. What it is

Five separable things. Knowing which one you are using answers most questions
about what the Community Edition can and cannot do.

| Component | What it is | What it works on |
| --- | --- | --- |
| **Python engine** (`src/intralogistics_flow_analyzer`) | A deterministic library, Python 3.10+, standard library only. Dijkstra on the aisle graph, route reconstruction, distance attribution, ABC/XYZ, co-pick affinity, a nine-check constraint gate and a replay simulator. | Any canonical dataset you give it, including your own industrial data. |
| **CLI** (`ifa`) | The engine's command line: `normalize`, `validate`, `analyze`, `recommend`, `simulate`, `run-evals`. | **Your real structured exports, locally.** This is how industrial data is analysed today. |
| **Skill** (`skills/…`) | A workflow and a vocabulary for an AI agent, so a fact, a calculation, a simulation and a hypothesis never get mixed up. | Whatever data the agent is given. |
| **Plugin manifests** (`plugin.json`, `mcp.json`, compatibility manifests) | Packaging that lets a Codex or Claude runtime load the skill and the demonstration server. | — |
| **Demonstration MCP server** (`mcp/server.py`) | Six read-only tools, no network access, no path accepted from a caller. | **Only the fictional datasets under `mcp/mock_data`.** |

Two consequences worth stating plainly:

* **Structured industrial data can already be analysed locally through the CLI.**
  Nothing about the engine is a demo: it is the same code path the reports in
  this repository come from.
* **The public MCP server deliberately reaches only the fictional datasets.** It
  exists to show what the engine produces, not to expose your warehouse to a
  model. That is a boundary of this edition, not a limitation of the engine.

Everything else — verified mappings against live master data, read-only WMS/ERP
connections, site-specific rules, access control and validation on the floor —
belongs to an industrial implementation.

And, to be explicit: the Community Edition is not a WMS, it never modifies your
source data, and it produces no measure of any individual's performance.

## 3. What it is not

* not a WMS;
* not transactional stock management;
* not inventory management;
* not a location-reservation system;
* not a tool for monitoring individual employees;
* not a global mathematical optimiser claiming to find the one best layout;
* not a ready-made connector to every ERP or WMS.

It is a local analysis and decision-support layer on top of structured exports.

## 4. Community Edition scope

The Community Edition is locally executable, open source and validated against
deterministic fictional regression cases.

In scope: canonical data contract, explicit CSV normalisation, deterministic
validation, route reconstruction, flow metrics, ABC/XYZ, co-pick affinity, SVG
flow map and spaghetti diagram, constraint-checked pair swaps, historical replay
simulation, JSON/CSV/Markdown/HTML reports, a local demo MCP server, and a
regression suite.

Out of scope in 0.1.2: live system connections, multi-location picking faces,
reserve-location optimisation, pick-sequence re-optimisation, batching, labour
or cost models, and anything resembling individual performance measurement.

Industrial implementations extend the same deterministic core with real data
mappings, read-only WMS/ERP connections, site-specific constraints, security and
contextual validation.

## 5. Architecture

```
CSV exports
    |
    v  ifa normalize   (explicit mapping.json, no guessing)
dataset.json (canonical)
    |
    v  ifa validate    (exit 0 / 1 / 2)
validated dataset
    |
    v  ifa analyze     graph -> routes -> metrics -> classes -> affinities
    |                  -> constraints -> simulation -> findings
    v
analysis.json  recommendations.csv  report.md  report.html
flow-map.svg   spaghetti.svg        moves.json
```

Modules, one responsibility each:

| Module | Responsibility |
| --- | --- |
| `models.py` | canonical data model, strict parsing |
| `loaders.py` | reading and writing JSON/CSV |
| `normalizer.py` | mapping-driven CSV to canonical dataset |
| `validation.py` | deterministic checks, status derivation |
| `graph.py` | directed weighted graph, Dijkstra, path cache |
| `routing.py` | order route reconstruction |
| `metrics.py` | totals, percentiles, attribution, edge flows |
| `classification.py` | ABC and XYZ |
| `affinity.py` | co-pick statistics |
| `constraints.py` | the nine hard checks |
| `slotting.py` | candidate generation and ranking |
| `simulation.py` | historical replay, before/after comparison |
| `findings.py` | traceable statements with claim types |
| `reporting.py` | analysis pipeline and report rendering |
| `evidence.py` | per-category evidence assessment |
| `svg.py` | flow map and spaghetti diagram |
| `cli.py` | the `ifa` command line |

Two helper scripts sit outside the package:

| Script | Responsibility |
| --- | --- |
| `scripts/build_release.py` | reproducible distribution archive, verified to exclude `.git`, caches and secrets |
| `scripts/validate_artifacts.py` | validates real artefacts against the three JSON Schemas (needs the `dev` extra) |

The engine deliberately avoids pandas, NumPy, SciPy, Matplotlib and NetworkX.
Nothing in a report depends on a library you would have to trust blindly.

## 6. Quick start

```bash
# From GitHub (the clone directory is industrial-flow-analyzer):
git clone https://github.com/neboltai/industrial-flow-analyzer.git
cd industrial-flow-analyzer
python3 -m pip install -e .

# From the release ZIP, the archive root is still named
# intralogistics-flow-analyzer (the Python package name). Unpack, then:
# cd intralogistics-flow-analyzer

# Optional: contract validation and the MCP interoperability test
python3 -m pip install -e '.[dev]' -e '.[mcp]'
```

Repository: <https://github.com/neboltai/industrial-flow-analyzer>  
Issues: <https://github.com/neboltai/industrial-flow-analyzer/issues>

See [PUBLICATION.md](PUBLICATION.md) for packaging and [EDITIONS.md](EDITIONS.md)
for Community and Industrial scope.

Then, on the fictional dataset shipped with the repository:

```bash
ifa normalize \
  --input examples/fictional-small-warehouse/raw \
  --mapping examples/fictional-small-warehouse/mapping.json \
  --output build/demo/dataset.json

ifa validate --dataset build/demo/dataset.json
ifa analyze  --dataset build/demo/dataset.json --output build/demo/analysis
ifa recommend --dataset build/demo/dataset.json --top 10 --output build/demo/recommendations
```

Open `build/demo/analysis/report.html` in any browser. It is a single static
file; it needs no server and no network.

Without installing, prefix the commands with `PYTHONPATH=src` and use
`python3 -m intralogistics_flow_analyzer` in place of `ifa`.

A second fictional layout, `examples/fictional-aisle-warehouse`, is a larger
aisle graph used to draw a readable spaghetti diagram. It does not replace the
historical regression dataset above. See that example's README for the exact
generation command. The engine does not import a floor-plan image; coordinates
and declared edge lengths are supplied in the dataset.

## 7. Data you need

Six blocks. The canonical form is defined in `schemas/dataset.schema.json` and
explained in `skills/intralogistics-flow-analyzer/references/data-contract.md`.

| Block | Minimum |
| --- | --- |
| `meta` | dataset id, period, `sequence_basis`, `distance_basis` |
| `layout.nodes` | node id, coordinates, node type |
| `layout.edges` | edge id, both endpoints, **declared distance**, direction, allowed modes |
| `locations` | location id, access node, type, and the constraint fields you want checked |
| `items` | SKU, and the constraint fields you want checked |
| `assignments` | SKU to location, role `pick` or `reserve` |
| `picks` | pick id, order id, **pick sequence**, SKU, location, quantity |

Two `meta` fields decide how a result may be *described*:

* **`sequence_basis`** — `planned`, `scan_confirmed`, `observed` or `unknown`.
  Only `scan_confirmed` and `observed` support a statement about the order
  actually walked; the others describe an intention. Omitting it reads as
  `unknown`, which is backward compatible and conservative.
* **`distance_basis`** — `declared_graph_shortest_path` in schema 1.0. The engine
  models distances, it never measures movement.

Two other fields decide how much you get out at all:

* **`distance_m` on every edge.** Without it there is no distance analysis, and
  a straight line between coordinates is not a substitute — it crosses racks.
* **`pick_sequence` on every pick.** Without it, frequency and affinity still
  work, route reconstruction does not, and the engine says so instead of
  inventing a number.

Worker-identifying columns are dropped during normalisation and never reach the
dataset or a report.

## 8. CLI commands

| Command | What it does | Exit codes |
| --- | --- | --- |
| `ifa normalize` | CSV + mapping to canonical dataset | 0, 1 |
| `ifa validate` | deterministic checks | 0 valid, 1 blocking error, 2 partially analysable |
| `ifa analyze` | full pipeline and every report | mirrors validate |
| `ifa recommend` | constraint-checked, simulated swaps only | mirrors validate |
| `ifa simulate` | replay against an explicit move list | mirrors validate |
| `ifa run-evals` | deterministic regression cases | 0 pass, 1 fail |
| `ifa describe` | dataset summary, no analysis | 0 |

Two maintenance scripts complete the set:

| Script | What it does |
| --- | --- |
| `python scripts/validate_artifacts.py` | validates the generated JSON against the schemas, Draft 2020-12 |
| `python scripts/build_release.py` | builds and verifies `dist/intralogistics-flow-analyzer-<version>.zip` |

## 9. Generated files

| File | Content |
| --- | --- |
| `analysis.json` | the complete machine-readable result |
| `recommendations.csv` | one row per accepted swap, with all nine checks |
| `report.md` | the readable report, eleven sections |
| `report.html` | the same report as one self-contained static file |
| `flow-map.svg` | layout with traversal intensity per aisle segment |
| `spaghetti.svg` | the longest reconstructed routes |
| `spaghetti-aggregate.svg` | all routes collapsed into per-segment intensity |
| `moves.json` | the accepted swaps, ready for `ifa simulate --moves` |

## 10. Evidence rules

1. A high pick frequency is not evidence of a badly placed item.
2. A long route is not evidence of avoidable travel.
3. A heavily traversed aisle is not evidence of congestion.
4. Co-occurrence of two items is not evidence that they belong next to each other.
5. A simulated reduction is not a realised saving.
6. The cost of physically relocating stock stays separate from any potential gain.
7. Replenishment distance stays separate from picking distance.
8. No slotting change is proposed while a critical constraint value is missing.
9. Every scenario names the historical orders it replayed.
10. Every conclusion traces back to a record or a calculation in the report.

Reports separate **Fakt**, **Berechnung**, **Simulation**, **Hypothese** and
**Fehlende Evidenz**. The measure of a proposed swap is always an
*estimated pick-distance reduction*, never a guaranteed saving, and the
recommendation set is never described as an optimum.

## 11. Tests

```bash
python -m unittest discover -s tests -v   # unit tests
ifa run-evals                             # regression cases, per category
python mcp/smoke_test.py                  # MCP protocol and safety smoke test
python scripts/validate_artifacts.py      # real JSON Schema validation (dev extra)
python scripts/build_release.py           # build and verify the release archive
```

The unit suite covers validation, graph and routing, metrics, ABC/XYZ, affinity,
constraints, slotting, simulation, normalisation, reporting and the CLI. The
eval cases pin the behaviour that must not drift; see `tests/evals/README.md`.

Current counts are reported in `CHANGELOG.md` for the released version, and by
the commands above for your working copy.

## 12. Security and privacy

* No real data, no company name, no employee identifier, no token, no secret and
  no customer endpoint in this repository.
* The engine produces no individual performance measure of any kind. The unit of
  analysis is the order, the route, the item, the location and the zone.
* The demonstration MCP server serves only the fictional datasets under
  `mcp/mock_data`, has no network access and accepts no file path from the
  caller. It speaks both MCP eras: the modern revision `2026-07-28`
  (per-request `_meta`, `server/discover`, `UnsupportedProtocolVersionError`) and
  the `initialize` handshake revisions `2025-11-25`, `2025-06-18`, `2025-03-26`
  and `2024-11-05`. Those are exactly the versions the official Python SDK
  speaks, and `tests/test_mcp_protocol.py` drives the server with that SDK so the
  claim is tested rather than asserted.
* Simulation never modifies the source dataset.

See `SECURITY.md`.

## 13. Boundary with industrial integrations

The Community Edition stops where a real installation begins. It reads
structured exports you hand it, on your machine, and writes files next to them.

An industrial implementation adds what an export cannot give you: verified
mappings against the live master data, read-only WMS/ERP connections, site
constraints that exist only in a plant's head, access control, and validation of
the results against what actually happens on the floor. The arithmetic in this
repository does not change — the context around it does.

### Confidentiality of a real dataset

Removing the worker columns does **not** make a warehouse dataset anonymous, and
it was never meant to. A real export still carries commercially sensitive
material:

* order identifiers, which can be joined back to customers;
* SKUs, volumes and weights, which describe your product range;
* the layout itself, which describes your site;
* order profiles and seasonality, which describe your business.

Treat a real `dataset.json`, `analysis.json` and `report.html` as internal
documents. Keep them out of version control, out of shared drives, and out of any
model context you do not control. Pseudonymisation of order and SKU identifiers
is on the v0.2 roadmap; this version does not attempt it.

## 14. Licence

MIT. See `LICENSE`.

Author: Sylvain Gerbe — <https://nebolt.ai>

## Release verification

Python 3.10–3.14 is the validation matrix. Install `.[dev,mcp]` and run
`python scripts/check_release.py`; skipped tests fail this gate. The deterministic
engine still has no mandatory dependencies. The local MCP demo is not a public
OpenAI directory submission. See PUBLICATION.md for packaging and hosted-service
requirements.
