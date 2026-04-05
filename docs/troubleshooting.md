# Troubleshooting

Common setup and runtime issues.

---

## Ollama model pull fails

**Symptoms:** `docker compose up` hangs or exits with a pull error from the Ollama container.

**Causes and fixes:**

- **Disk space** — model files are several GB. Run `df -h` and free space if needed.
- **Network** — Ollama pulls from `registry.ollama.ai`. Check that outbound HTTPS is not blocked.
- **Retry** — run `docker compose restart ollama` and check logs with `docker compose logs -f ollama`.

---

## `ping()` failure on startup — Qdrant or Postgres unreachable

**Symptoms:** Orchestrator exits immediately with `STARTUP FAILED: <backend> is unreachable`.

**Causes and fixes:**

- Qdrant and Postgres are hard requirements — the orchestrator will not start if either is unreachable. With Docker-first healthchecks, `depends_on: condition: service_healthy` should prevent this on a clean start.
- If it still occurs: run `docker compose ps` to see which service is unhealthy.
- Check the failing service's logs: `docker compose logs -f <service>`.
- Restart the full stack: `docker compose up -d`. Services restart automatically (`restart: unless-stopped`).

---

## Embedding model not available — search and ingest unavailable

**Symptoms:** `GET /` returns `"embedding_model_ready": false`. Search, ingest, and RAG return HTTP 503.

**Cause:** The embedding model (`nomic-embed-text` by default) has not been pulled into Ollama yet. On first start, the orchestrator entrypoint script pulls it automatically — but this can fail on slow or metered connections.

**Fixes:**

1. Check the entrypoint log: `docker compose logs orchestrator | grep entrypoint`
2. Pull manually via the dashboard: open **http://localhost:8000/dashboard → Settings → Models** and click Pull next to `nomic-embed-text`.
3. Or pull via CLI: `docker exec lumogis-ollama-1 ollama pull nomic-embed-text`
4. After a successful pull, click **Restart** in the dashboard, or run `docker compose restart orchestrator`.

---

## GPU not detected by Docker

**Symptoms:** GPU is available on the host but Docker containers cannot see it.

**Causes and fixes:**

- `nvidia-container-toolkit` must be installed and the Docker daemon restarted after installation. See the [NVIDIA Container Toolkit install guide](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/install-guide.html).
- Verify with: `docker run --rm --gpus all nvidia/cuda:12.0-base nvidia-smi`
- Check driver version: `nvidia-smi` on the host. Driver must support the CUDA version used by the image.

---

## LibreChat cannot reach the orchestrator

**Symptoms:** LibreChat shows a connection error when sending messages.

**Causes and fixes:**

- Both services must be on the same Docker Compose network. Check `docker network inspect lumogis_default` (or the network name defined in `docker-compose.yml`).
- Confirm the orchestrator URL in LibreChat's config matches the service name in Compose (e.g. `http://orchestrator:8000`).
- Run `docker compose logs -f librechat` for the specific error.

---

## Qdrant vector dimension mismatch

**Symptoms:** Qdrant returns a `{"status": "error", "result": null}` on search or upsert after changing the embedding model.

**Cause:** Each Qdrant collection is created with a fixed vector size. Switching embedding models (e.g. from Nomic Embed 768-dim to a 1536-dim model) requires dropping and recreating the collections.

**Fix:**

```bash
# Stop the stack
docker compose down

# Delete the Qdrant volume
docker volume rm lumogis_qdrant_data   # adjust name if different

# Restart — collections are recreated on orchestrator startup
docker compose up
```

All previously indexed documents will need to be re-ingested.

---

## Dashboard “Save & restart” fails (stack-control / Docker socket)

**Symptoms:** `permission denied while trying to connect to the docker API at unix:///var/run/docker.sock` in `docker compose logs stack-control`, or a restart error in the dashboard that now includes the stack-control message.

**Cause:** The `stack-control` service must mount the host’s Docker socket. The image uses an **entrypoint** that reads the socket’s group at **container start** and adds the `appuser` to that group so `docker compose restart` works on Linux, Docker Desktop (macOS/Windows), and different distros without setting `DOCKER_GID` in `.env`.

**Fixes:**

- Confirm compose still has `- /var/run/docker.sock:/var/run/docker.sock` for `stack-control`.
- **Recreate** the container after upgrading: `docker compose build stack-control && docker compose up -d stack-control`.
- If you use a **custom** socket path, mount it at `/var/run/docker.sock` inside the container or adjust the stack-control service (advanced).
