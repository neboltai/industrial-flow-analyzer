# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.1.2] — 2026-09-19

- Public repository URLs recorded: https://github.com/neboltai/industrial-flow-analyzer
  (Issues: https://github.com/neboltai/industrial-flow-analyzer/issues).
- Added `examples/fictional-aisle-warehouse`, a separate fictional layout used to
  reproduce a landing-page spaghetti diagram. The historical regression dataset
  `fictional-small-warehouse` is unchanged.
- Strict modern MCP metadata, ID and legacy handshake validation; valid
  notifications remain silent and do not execute tools.
- Recursive tool-argument checks including closed pair_swap objects; internal
  exceptions logged on stderr and redacted for clients.
- Portable Agent Plugins manifests alongside Codex/Claude compatibility.
- Corrected agent metadata and limited suggested prompts to three.
- Archive-independent git-preservation regression, mandatory no-skip checks,
  Python 3.10–3.14 CI, clean archive installation and reproducible checksums.
- Community/Industrial scope, support and publication checklist documented.
- Business formulas and fictional datasets unchanged. MIT unchanged.

## [0.1.1] — 2026-09-16

A consolidation release. No new analysis feature, no change to any formula, and
no change to any number the fictional example produces. What changed is how the
project is packaged, how precisely it describes its own figures, and how much of
that is checked automatically.

### Fixed

- **The release archive no longer contains `.git`.** Packaging was an ad-hoc
  `zip` invocation; it is now `scripts/build_release.py`, which walks an explicit
  include list, excludes `.git`, `.github`, `dist`, `build`, virtualenvs, caches
  and credential-shaped files, and then re-opens the finished archive and fails
  if anything forbidden is inside it. The build is reproducible: fixed entry
  order, fixed timestamps and fixed permissions give byte-identical rebuilds.
- **Evidence quality no longer labels every finding the same way.** One coverage
  rule used to qualify a two-order co-pick pair and a fifteen-order distance
  calculation identically. Each finding family now has its own deterministic
  ladder.
- **The hard-coded `high` on rejected candidates is gone.** Constraint findings
  are capped at `medium` while nothing has been verified on site.
- **The CI Python-cache check actually fails.** It printed a message and passed.
- **The Codex manifest no longer duplicates the MCP server definition.** It
  points at `./.mcp.json`, the single source of configuration.

### Added

- `meta.sequence_basis` (`planned`, `scan_confirmed`, `observed`, `unknown`) and
  `meta.distance_basis` (`declared_graph_shortest_path`). Both are additive: a
  schema 1.0 document written before they existed loads unchanged and receives
  the conservative defaults. Both appear in every report and in
  `analysis.thresholds`.
- `evidence_assessment` on every finding: the factors that produced the label,
  the `site_verified` flag, a one-sentence rationale and the caps that were
  applied. `evidence_quality` is kept for 0.1.0 consumers.
- `src/intralogistics_flow_analyzer/evidence.py` with one function per finding
  family — routes, ABC, XYZ, affinity, simulation, constraints — each with
  documented, configurable thresholds echoed into `analysis.thresholds.evidence`.
- Two mandatory caps: an unverified simulation never reaches `high`, and complete
  but unverified constraint data never reaches `high`.
- `scripts/validate_artifacts.py`: real Draft 2020-12 validation of the example
  dataset, a freshly normalised dataset, the MCP mock data, `analysis.json`,
  `recommendations.json` and every eval case that embeds a dataset.
- `dev` extra (`jsonschema`, `PyYAML`) and `mcp` extra (the official SDK). The
  engine itself still has no runtime dependency.
- German worker-column variants — `mitarbeiter_id`, `mitarbeiter_name`,
  `mitarbeiternummer`, `personalnummer`, `personal_nr`, `kommissionierer`,
  `kommissionierer_id`, `kommissionierer_name`, `bediener_id`, `bediener_name`,
  `benutzer_id`, `benutzername`, `lagerarbeiter`, `ausweisnummer` — are dropped
  and reported like their English equivalents.
- `SUSPECTED_PERSONAL_COLUMN_NOT_MAPPED`: a column that merely looks personal is
  reported by name rather than removed on a guess.
- Guidance, in `README.md` and `SECURITY.md`, that removing the worker columns
  does not make a real dataset anonymous: order identifiers, SKUs, volumes,
  layout and order profiles may all be commercially sensitive.
- 124 new tests (295 in total) and 5 new eval cases (18 in total), covering the
  four `sequence_basis` values and their absence, every evidence family and
  level, both mandatory caps, positive and negative schema validation, both MCP
  eras, archive hygiene and manifest consistency.

### Changed

- **The demonstration MCP server is dual-era.** It previously declared the
  legacy `2024-11-05` only. It now serves the modern revision `2026-07-28`
  (per-request `_meta`, mandatory `server/discover`, `UnsupportedProtocolVersionError`
  with code `-32022` listing the supported versions, `resultType`, and the
  caching hints `ttlMs` and `cacheScope` on cacheable results) **and** the
  `initialize` handshake revisions `2025-11-25`, `2025-06-18`, `2025-03-26` and
  `2024-11-05`. Those are exactly the versions the official Python SDK speaks.
  An `initialize` at a modern version is refused with an explanation rather than
  faked. Diagnostics go to stderr; stdout carries MCP messages only.
- `tests/test_mcp_protocol.py` drives the server with the **official MCP Python
  SDK** in both eras, so protocol conformance is tested rather than asserted.
  That test is what found the missing caching hints.
- Distance vocabulary is exact everywhere: *modelled graph distance*,
  *reconstructed shortest-path distance*, *modelled segment traversals*. The
  phrase *actual walked distance* is not used, and an eval case enforces it.
  Numeric field names such as `total_distance_m` are unchanged.
- Report tables are English. `Fakt`, `Berechnung`, `Simulation` and `Hypothese`
  appear once each, in the glossary that introduces the claim types.
- The flow map distinguishes route start, route end, depot, packing, shipping
  and receiving with separate markers, and draws an arrowhead **only** on
  genuinely one-way segments. The spaghetti diagram states how many routes of
  how many it shows.
- The findings register shows why each evidence level was reached.
- `README.md` separates the five components — engine, CLI, Skill, plugin
  manifests, demonstration MCP server — and states positively that real
  structured exports are analysed locally through the CLI while the public MCP
  server deliberately reaches only the fictional datasets.
- The OpenAI `default_prompt` is no longer limited to the fictional dataset; a
  demonstration starter prompt is kept alongside it.
- CI runs on Python 3.10, 3.11 and 3.12, installs both extras, validates the
  artefacts against the schemas, builds and inspects the release archive, and
  checks that the fictional example still produces 1 560.00 m and SWAP-001.
- `dist/` is git-ignored.

### Verified unchanged

`orders reconstructed 15 / 15` · `total distance 1 560.00 m` ·
`median 100.00 m` · `p90 120.00 m` · `1 recommendation` ·
`SWAP-001 reduction 80.00 m / 5.1282 %`.

### Deferred to v0.2

Pseudonymisation of order and SKU identifiers; German/English report generation;
an interactive dashboard.

## [0.1.0] — 2026-09-16

First public Community Edition release.

### Added

**Data contract**

- Canonical `dataset.json` schema 1.0 with `meta`, `layout`, `locations`,
  `items`, `assignments`, `picks` and `config`.
- JSON Schema definitions for the dataset, the analysis result and the
  recommendations document under `schemas/`.

**Normalisation**

- `ifa normalize`: mapping-driven CSV to canonical dataset. Units, column
  meanings, aisle directions, temperature zones, hazard classes and capacities
  are never inferred; an ambiguous mapping raises a normalisation question.
- Only purely syntactic transforms are permitted, and each is named in the
  mapping.
- Worker-identifying columns are dropped before parsing and reported.

**Validation**

- `ifa validate` with exit codes 0 (valid), 1 (blocking error) and 2 (partially
  analysable), and the statuses `ok`, `partial`, `insufficient_evidence` and
  `data_error`.
- Checks for duplicate identifiers, dangling references, invalid edges, a
  disconnected graph, missing access nodes, unknown SKUs and locations,
  duplicated pick sequences, invalid quantities and capacities, items too heavy
  for their location, temperature and hazard incompatibilities, forbidden
  handling modes, multiple pick assignments, timestamps outside the declared
  period, impossible routes and unknown units.
- Nothing is repaired silently; every issue carries a code, a severity, a field
  and the affected records.

**Graph and routing**

- Directed, mode-aware, weighted aisle graph; bidirectional edges become two
  arcs.
- Dijkstra over `heapq` with a single-source path cache per `(source, mode)` and
  deterministic tie-breaking.
- Route reconstruction from `pick_sequence` only. The file row order is never
  used as a substitute; an order without a sequence is not routed, and an order
  with an unreachable leg is excluded and named.

**Metrics**

- Order, line and unit totals; total, mean, median and p90 distance per order;
  distance per pick line; zone transitions; unreachable order count.
- Per-location and per-SKU visit frequency and attributable distance, per-edge
  traversals, zone flows, top 10 orders and top 10 locations by distance.
- Percentiles use linear interpolation between closest ranks, documented and
  echoed into the report.

**Classification**

- Configurable ABC on `pick_lines`, `units` or `orders`, with the crossing item
  belonging to the class it completes.
- XYZ on the coefficient of variation of per-bucket demand, computed only when
  enough buckets exist; otherwise the marker
  `XYZ_NOT_COMPUTED_INSUFFICIENT_PERIODS`. Missing data never becomes class Z.

**Affinity**

- Co-pick count, support, both confidences and lift, with every denominator
  checked. Results are filtered by configurable thresholds and always carry the
  statement that co-occurrence is not causality.

**Visualisation**

- `flow-map.svg` and `spaghetti.svg`, produced with the standard library, each
  with `<title>` and `<desc>`, no external resource, and a sober configurable
  palette.
- The spaghetti diagram draws one order, the N longest orders, or the aggregated
  flow, with the number of drawn routes capped for readability.

**Slotting and simulation**

- Nine hard constraint checks per candidate swap: volume, total weight, unit
  weight, temperature, hazard class, handling mode, zone, level and fixed. An
  unknown value blocks the move and returns `insufficient_constraint_data`.
- `ifa recommend`: candidate generation, constraint gating, full historical
  replay per candidate, ranking by calculated reduction, and a list of rejected
  candidates with the check that blocked each one.
- `ifa simulate`: replay against an explicit move list. The source dataset is
  never modified.
- The measure is always an *estimated pick-distance reduction*; the wording
  "guaranteed saving" is not used, and the result set is never called an optimum.

**Reporting**

- `analysis.json`, `recommendations.csv`, `report.md`, `report.html`,
  `flow-map.svg`, `spaghetti.svg`, `spaghetti-aggregate.svg` and `moves.json`.
- The HTML report is one self-contained static file: no external resource, no
  script required for the content, printable and responsive, with a fictional
  dataset clearly labelled.
- Findings carry `claim_type` (`observed`, `calculated`, `simulated`,
  `hypothesis`) and `evidence_quality` (`high`, `medium`, `low`,
  `insufficient`), with assumptions and limitations attached.

**Packaging and integration**

- `pyproject.toml` with the `ifa` entry point; the package also runs as
  `python -m intralogistics_flow_analyzer`.
- Agent skill with five reference documents and a report template.
- `.codex-plugin/plugin.json`, `.claude-plugin/plugin.json` and `.mcp.json`.
- Local demonstration MCP server over JSON-RPC 2.0 on stdio exposing
  `list_demo_datasets`, `get_demo_summary`, `validate_demo_dataset`,
  `analyze_demo_flows`, `recommend_demo_slotting` and `simulate_demo_moves`,
  restricted to an allowlist of fictional datasets, with no network access.
- Example multi-provider agent runtime configuration using environment variable
  names only.

**Quality**

- 171 deterministic unit tests across validation, graph, routing, metrics,
  classification, affinity, constraints, slotting, simulation, normalisation,
  reporting and the CLI.
- 13 eval cases across validation, routing, metrics, classification, constraint
  handling and unsupported claims, run by `ifa run-evals`.
- 13-check MCP smoke test.
- GitHub Actions workflow on Python 3.10 and 3.12: syntax compilation, JSON
  validation, unit tests, evals, MCP smoke test, example report generation and
  output verification. Nothing is published automatically.

**Fictional dataset**

- `fictional-small-warehouse`: 12 nodes, 16 edges, 10 pick locations, 2 reserve
  locations, 8 SKUs, 15 orders, 54 pick lines, two travel modes, an ambient and
  a chilled zone, a pinned item, a heavy item and a strong co-pick pair.
- It is built so that one swap is valid and reduces the simulated distance, and
  so that other candidates are blocked by weight, by temperature, by hazard
  class, by a fixed location, or by the absence of a material improvement.

### Known limitations in 0.1.0

- Only swaps between two pick locations; no location creation, no group move of
  more than two SKUs, no global optimum.
- Reserve locations are represented but not optimised.
- Replenishment travel, put-away travel and relocation effort are outside the
  model.
- Congestion, queueing, shift patterns and equipment availability are not
  modelled.
- Pick sequencing and batching are taken as given and never re-optimised.
- One active pick assignment per SKU and one SKU per pick location.
- One travel mode per order; a mixed-mode order is reported and not routed.
- Distances are declared graph lengths, never measured movement.
