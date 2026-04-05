# GPU Acceleration for Lumogis

Lumogis uses Ollama for local model inference. GPU acceleration significantly speeds up
local model inference and is optional — Lumogis works without GPU in CPU-only mode.

## Enabling GPU

Add to your `.env`:

```
COMPOSE_FILE=docker-compose.yml:docker-compose.gpu.yml
```

Then restart:

```bash
docker compose up -d
```

---

## Linux (NVIDIA)

**Requirements:** NVIDIA GPU, NVIDIA drivers, NVIDIA Container Toolkit.

**Install NVIDIA Container Toolkit:**

```bash
# Add the NVIDIA Container Toolkit repository
curl -fsSL https://nvidia.github.io/libnvidia-container/gpgkey | \
  sudo gpg --dearmor -o /usr/share/keyrings/nvidia-container-toolkit-keyring.gpg

curl -s -L https://nvidia.github.io/libnvidia-container/stable/deb/nvidia-container-toolkit.list | \
  sed 's#deb https://#deb [signed-by=/usr/share/keyrings/nvidia-container-toolkit-keyring.gpg] https://#g' | \
  sudo tee /etc/apt/sources.list.d/nvidia-container-toolkit.list

sudo apt-get update && sudo apt-get install -y nvidia-container-toolkit
sudo nvidia-ctk runtime configure --runtime=docker
sudo systemctl restart docker
```

**Verify:**

```bash
docker run --rm --gpus all nvidia/cuda:12.0-base nvidia-smi
```

---

## Windows (NVIDIA via WSL2)

**Requirements:** NVIDIA GPU, NVIDIA drivers for WSL2, Docker Desktop with WSL2 backend.

Docker Desktop on Windows uses WSL2 as its backend. GPU passthrough works via the
NVIDIA drivers for WSL2 — you do **not** need to install CUDA separately inside WSL2.

**Steps:**

1. Install [NVIDIA drivers for Windows](https://www.nvidia.com/Download/index.aspx)
   (version 470.76+ supports WSL2 GPU passthrough)
2. Ensure Docker Desktop is using the WSL2 backend:
   Docker Desktop → Settings → General → "Use the WSL 2 based engine" ✓
3. Add `COMPOSE_FILE=docker-compose.yml:docker-compose.gpu.yml` to your `.env`
4. Run `docker compose up -d` from PowerShell

**Verify (from PowerShell):**

```powershell
docker run --rm --gpus all nvidia/cuda:12.0-base nvidia-smi
```

---

## macOS (Apple Silicon)

**Not supported.** Docker Desktop on macOS does not expose the Apple Silicon GPU
(Metal) to containers. Ollama runs in CPU-only mode inside the container.

**Alternative for better performance on macOS:**

Install Ollama natively on macOS (it uses Metal GPU acceleration natively), then
point Lumogis to the native Ollama instance:

1. Install Ollama: https://ollama.com/download/mac
2. In your `.env`:
   ```
   OLLAMA_URL=http://host.docker.internal:11434
   ```
3. Pull the embedding model:
   ```bash
   ollama pull nomic-embed-text
   ```
4. Run `docker compose up -d` without the GPU overlay

This gives you native Metal GPU acceleration for local models while keeping all
other Lumogis services containerized.

---

## AMD GPU (Linux)

AMD GPU support in Docker requires ROCm. This is experimental — check the
[Ollama documentation](https://github.com/ollama/ollama/blob/main/docs/gpu.md)
for the latest AMD support status and ROCm setup instructions.

The `docker-compose.gpu.yml` overlay is configured for NVIDIA only. For AMD,
you would need a custom overlay with the appropriate ROCm device configuration.
