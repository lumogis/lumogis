# lumogis-mock-capability (dev / contract smoke)

Tiny **non-product** HTTP capability: manifest + health + one tool. Used to prove
packaging boundaries for a **second** service (Phase 5 FU-4) without adding a
real premium feature.

## Not included in the default stack

Use the compose overlay at the repo root:

```bash
export MOCK_CAPABILITY_SHARED_SECRET="$(openssl rand -hex 24)"
docker compose -f docker-compose.mock-capability.yml up --build
```

Default listen: host port **18080** → container **8080**.

## Core wiring (manual / experimental)

1. Point Core discovery at the service base URL (e.g. `http://host.docker.internal:18080` from the orchestrator container, or the compose service name on a shared network).
2. Set `CAPABILITY_SERVICE_URLS` (or your usual capability URL list) to that base URL.
3. Set a per-service bearer for tool invocation, e.g.  
   `LUMOGIS_CAPABILITY_BEARER_LUMOGIS_MOCK_ECHO=<same as MOCK_CAPABILITY_SHARED_SECRET>`  
   (sanitized service id `lumogis.mock.echo` → env suffix `LUMOGIS_MOCK_ECHO`).
4. Optionally `LUMOGIS_TOOL_CATALOG_ENABLED=true` to surface `mock.echo_ping` in chat.

This service **must not** receive Core Postgres, Qdrant, or household filesystem credentials.

## Tests

```bash
cd services/lumogis-mock-capability
python3 -m venv .venv && .venv/bin/pip install -r requirements-dev.txt
.venv/bin/python -m pytest tests -q
```
