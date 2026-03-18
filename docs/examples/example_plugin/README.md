# Example plugin template

Minimal plugin for lumogis-core: subscribes to `Event.DOCUMENT_INGESTED`, logs each ingest, and exposes `GET /example/stats`.

## Use as a template

1. Copy this entire folder to `orchestrator/plugins/<your_name>/` (e.g. `orchestrator/plugins/myfeature/`).
2. Rename modules if you like; keep `__init__.py` with a `router` attribute if you add HTTP routes.
3. Rebuild or restart the orchestrator (`docker compose up -d --build` or `make dev`).

## Verify

After ingesting a document:

```bash
curl -s http://localhost:8000/example/stats | python3 -m json.tool
```

You should see `documents_ingested` increment.

## Rules

- Import only from `events`, `hooks`, `ports`, `models`, and standard library / your deps.
- Do **not** import `services`, `adapters`, or `config` from plugins.

## More examples

See [ARCHITECTURE.md](../../../ARCHITECTURE.md) and [CONTRIBUTING.md](../../../CONTRIBUTING.md).
