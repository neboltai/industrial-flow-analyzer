# Indulayer runtime bridge

Flow 0.1.2 can be exposed to the Indulayer control plane through a thin HTTP bridge.

The bridge does not duplicate routing, metrics, constraint or simulation logic. It imports the existing deterministic package and normalizes the result into the shared runtime run/evidence envelope.

Endpoints:

- `GET /health/ready`
- `POST /api/v1/analysis-runs`

Run:

```bash
export FLOW_RUNTIME_TOKEN='replace-me'
python -m intralogistics_flow_analyzer.runtime_server --host 127.0.0.1 --port 8082
```

The POST endpoint requires a bearer service token and accepts the product-native canonical Flow JSON dataset.

Automatic transport retry is intentionally not declared because persistent product-side idempotency is not yet implemented. Runtime integration is not field validation.
