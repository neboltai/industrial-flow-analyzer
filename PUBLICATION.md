# Publication checklist — 0.1.2

The public repository is:

- Repository: https://github.com/neboltai/industrial-flow-analyzer
- Issues: https://github.com/neboltai/industrial-flow-analyzer/issues

Those URLs are recorded in `pyproject.toml` and the plugin manifests.

## Remaining owner steps

1. Confirm the MIT license decision (unchanged in this package).
2. If distributing through a Claude marketplace, create its marketplace manifest
   against this repository URL.
3. Run the full CI matrix on GitHub Actions and inspect every required job.
4. Where Claude Code is available, run `claude plugin validate . --strict`.
5. Run the available OpenAI/Codex plugin validator. The portable Agent Plugins
   schemas and the Codex compatibility contract are distinct checks.
6. Run `python scripts/check_release.py` and `python scripts/build_release.py`
   twice; compare the ZIP SHA-256 values. Extract, install `.[dev,mcp]` in a fresh
   environment and repeat the checks from the extraction directory.
7. Publish a tagged GitHub Release and its ZIP/checksum only after explicit
   owner authorization. This package does not create tags or GitHub Releases by
   itself.

The distribution ZIP excludes `.github/`; the separate source ZIP includes CI
and issue templates. Neither includes `.git` or generated environments.

A `git clone` of the GitHub repository creates the directory
`industrial-flow-analyzer`. The Python package name and the release-archive root
remain `intralogistics-flow-analyzer`.

## Protocol sources consulted on 2026-09-19

- https://modelcontextprotocol.io/specification/2026-07-28/basic
- https://modelcontextprotocol.io/specification/2026-07-28/basic/versioning
- https://modelcontextprotocol.io/specification/2025-11-25/basic/lifecycle
- https://developers.openai.com/plugins/build/plugins
- https://developers.openai.com/plugins/build/mcp-server
- https://agent-plugins.org/schemas/1.0.0/plugin.schema.json
- https://agent-plugins.org/schemas/1.0.0/mcp.schema.json
- https://code.claude.com/docs/en/plugins-reference

Portable root manifests are canonical. The Codex overlay and `.mcp.json` retain
legacy packaging compatibility. Claude uses its own plugin-root variable.
After a valid legacy initialize, empty or legacy-only metadata remains legacy
(as used by the official SDK). Any modern reserved field triggers full modern
validation, independently of the legacy session. Discover always requires modern
metadata. An absent modern field cannot be filled from a previous request.

## GitHub description

Open-source deterministic intralogistics flow analysis: route reconstruction,
distance metrics, spaghetti diagrams, flow maps and constraint-safe slotting
simulations. Includes a local CLI, schemas, evals, an AI Skill and demo MCP server.

## Release notes

Community Edition 0.1.2 consolidates the existing engine without changing business
formulas. It adds strict MCP request and nested move validation, redacted internal
errors, modern/legacy SDK interoperability checks, portable plugin manifests,
archive-independent tests and a no-skip release gate. Community and Industrial
scope are documented. MIT remains unchanged. The demo still reconstructs 15/15
orders, models 1560 m total distance (median 100 m, p90 120 m) and proposes one
swap with an estimated 80 m / 5.1282% reduction.
