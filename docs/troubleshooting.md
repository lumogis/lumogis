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

## `ping()` failure on startup

**Symptoms:** Orchestrator exits immediately with `STARTUP FAILED: <backend> is unreachable`.

**Causes and fixes:**

- The named service (Qdrant, PostgreSQL, Ollama, or LiteLLM) has not finished initialising yet. Docker Compose `depends_on` does not wait for the service to be *ready*, only started.
- Run `docker compose ps` to check service health.
- Restart just the orchestrator: `docker compose restart orchestrator`.
- Check the failing service's logs: `docker compose logs -f <service>`.

---

## GPU not detected by Docker

**Symptoms:** `scripts/detect-hardware.sh` shows GPU available but Docker containers cannot see it.

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
