# Security and privacy

## What this repository contains

* **No real data.** Every dataset shipped here is fictional and carries
  `meta.fictional: true`. No real company, site, customer or person appears
  anywhere.
* **No secrets.** No API key, token, password or credential. The example runtime
  configuration names environment variables only; it never contains a value.
* **No production connections.** There is no WMS connector, no ERP connector, no
  database driver and no customer endpoint. The engine reads files you give it
  and writes files next to them.
* **No individual scoring.** The analyser produces no performance measure of any
  individual, and no data structure in which one could be computed.

## Worker data

Columns that identify a person — `employee_id`, `employee_name`, `worker_id`,
`worker_name`, `picker_id`, `picker_name`, `operator_id`, `operator_name`,
`user_id`, `user_name`, `staff_id`, `staff_name`, `badge_id`, and the German
equivalents `mitarbeiter_id`, `mitarbeiter_name`, `mitarbeiternummer`,
`personalnummer`, `personal_nr`, `kommissionierer`, `kommissionierer_id`,
`kommissionierer_name`, `bediener_id`, `bediener_name`, `benutzer_id`,
`benutzername`, `lagerarbeiter`, `ausweisnummer` — are:

* dropped by the normalizer before any parsing;
* reported as unnecessary for flow analysis;
* never written to a canonical dataset or a report;
* never usable to compare people.

Mapping such a column on purpose is refused with an error. The unit of analysis
is the order, the route, the item, the location and the zone.

## The demonstration MCP server

`mcp/server.py` is a local demonstration server. By design it:

* serves only the fictional datasets under `mcp/mock_data`, resolved through an
  allowlist built by scanning that directory at startup;
* accepts no file path from the caller — a `dataset_id` outside the allowlist is
  refused, including anything that looks like a path;
* opens no network connection;
* connects to no WMS, ERP or external service;
* holds no secret and no endpoint;
* writes to no external system;
* never modifies a dataset on disk, including during simulation.

`mcp/smoke_test.py` checks each of those properties on every CI run.

## What removing the worker columns does not do

Dropping `picker_name` and its equivalents removes *personal* data. It does not
make the dataset anonymous, and it does not make it safe to share.

A real export still describes your business:

* **order identifiers** can often be joined back to a customer;
* **SKUs, volumes and weights** describe your product range;
* **the layout** — nodes, edges, zones, capacities — describes your site;
* **order profiles** describe your demand, your seasonality and your customers'
  buying patterns.

Any of that may be commercially sensitive or contractually confidential. Treat a
real dataset and every report derived from it as internal material.

Pseudonymisation of order and SKU identifiers is on the v0.2 roadmap. Version
0.1.2 does not attempt it, and claiming otherwise would be worse than not doing
it at all.

## Columns that only look personal

Besides the exact worker-identifying column names it drops, the normalizer also
flags columns whose *name* suggests a person — anything containing `employee`,
`worker`, `picker`, `operator`, `mitarbeiter`, `personalnummer`,
`kommissionierer`, `bediener`, `ausweis` or `badge`. Such a column is never
removed on a guess: it is reported as `SUSPECTED_PERSONAL_COLUMN_NOT_MAPPED`, and
it stays out of the canonical dataset only because the mapping does not claim it.
Every column the normalizer leaves behind appears in the normalisation report.

## Running it on real data

The engine is local and offline. If you point it at a real export:

* the export leaves your machine only if you move it yourself;
* keep the export and any generated report outside version control;
* strip worker identifiers at the source as well, not only at normalisation;
* treat `analysis.json` and `report.html` as internal documents — they describe
  your site layout and your order profile.

## Recommendations must be validated before anything moves

A recommendation from this tool is an **estimated pick-distance reduction**
computed by replaying historical orders with an unchanged pick sequence. It is
not a realised saving, it excludes replenishment travel and relocation effort,
and it does not model congestion or ergonomics.

Validate every proposed change on site — stock volume, access, reachability,
safety and handling equipment — before physically moving anything.

## Reporting a vulnerability

Please report suspected vulnerabilities to **contact@nebolt.ai**.

Include what you found, how to reproduce it, and the impact you expect. Please do
not open a public issue for a security problem before it has been addressed.
Reports are acknowledged and handled as quickly as reasonably possible; this is
an open-source project maintained without a paid support commitment.
