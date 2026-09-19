# Contributing

Thanks for looking at this. The project has a narrow shape on purpose; the notes
below explain what that shape is, so a pull request does not run into it by
surprise.

## Principles

1. **The engine is deterministic.** The same dataset must produce byte-identical
   output on every run and on every supported Python version. Iteration order,
   tie-breaking and rounding are all fixed deliberately.
2. **The standard library is the default.** pandas, NumPy, SciPy, Matplotlib and
   NetworkX are out of scope for 0.x. Every algorithm should stay readable and
   auditable inside this repository.
3. **Nothing is guessed.** A missing unit, an ambiguous column, an unknown
   capacity: the engine asks or refuses, it never picks a plausible default.
4. **Nothing is repaired silently.** Inconsistencies become issues with a stable
   code, a severity, a field and the affected records.
5. **Claims stay labelled.** Observed, calculated, simulated and hypothesis are
   different things and are never merged in an output.
6. **No individual worker data.** Ever, anywhere, for any reason.

## Setting up

```bash
python3 -m pip install -e ".[dev,mcp]"
python -m unittest discover -s tests -v
ifa run-evals
python mcp/smoke_test.py
```

The engine needs only the standard library. Release validation requires both
the development and MCP extras; skipped tests fail `scripts/check_release.py`.

## Before opening a pull request

Run all of it:

```bash
python -m compileall -q src mcp tests
python -m unittest discover -s tests -v
ifa run-evals
python mcp/smoke_test.py
ifa analyze --dataset examples/fictional-small-warehouse/dataset.json --output build/check
```

and check that:

* every JSON file still parses;
* no file contains a secret, a real company name or a worker identifier;
* no file contains an unfinished marker such as `TODO`, `TBD`, `PLACEHOLDER` or
  `Coming soon` — the only permitted occurrences are inside an example that
  explicitly exists to detect them;
* no Python cache directory is committed.

## Changing a number

If a change alters a computed figure, it must also update the eval case that
pins it. A silent change to a published metric is a defect even when the new
number is better: someone has a report with the old one.

Add an eval case whenever you add a rule. A rule without a case that fails when
the rule is removed is not really enforced.

## Code style

* Type hints everywhere, `from __future__ import annotations` at the top.
* Module docstrings explain the *why* and state the formula being implemented.
* Names spell things out: `estimated_reduction_m`, not `red`.
* Keep functions pure where you reasonably can, and never mutate a dataset that
  was passed in.
* Lines wrap at 100 characters.

## Wording

The vocabulary is part of the product. Please keep it:

* "estimated pick-distance reduction", never "saving" or "guaranteed";
* "high concentration of traversals", never "congested" without time evidence;
* "appear together in N orders", never "must be stored together";
* "candidate swap", never "optimal layout".

## Scope of 0.1.2

Out of scope for now, by design: live system connectors, multi-location picking
faces, reserve-location optimisation, pick-sequence re-optimisation, batching,
labour and cost models, and a global layout optimiser. A pull request adding one
of those is welcome as a discussion first — it changes what the tool claims to
be, which is a bigger decision than the code.

## Licence

By contributing you agree that your contribution is licensed under the MIT
licence in `LICENSE`.
