# Mock data for the demonstration MCP server

Every directory here that contains a `dataset.json` becomes one entry in the
server's allowlist, and its directory name becomes the `dataset_id` that the six
tools accept. The server builds that allowlist by scanning this folder at
startup and will read nothing else — a `dataset_id` outside it is refused,
including anything that looks like a file path.

| Directory | Source |
| --- | --- |
| `fictional-small-warehouse` | identical copy of `examples/fictional-small-warehouse/dataset.json` |

Refresh the copy after changing the example:

```bash
python -m intralogistics_flow_analyzer normalize \
  --input examples/fictional-small-warehouse/raw \
  --mapping examples/fictional-small-warehouse/mapping.json \
  --output examples/fictional-small-warehouse/dataset.json

cp examples/fictional-small-warehouse/dataset.json \
   mcp/mock_data/fictional-small-warehouse/dataset.json
```

CI fails if the two copies drift apart.

Everything in this folder is fictional and carries `meta.fictional: true`. Real
data, a real company name or a worker identifier must never be placed here.
