#!/usr/bin/env bash
# Detects local hardware and determines the appropriate Ollama model tier.
# Outputs shell-evaluable LG_* variables to stdout.
# Human-readable summary and errors go to stderr.
#
# Usage:
#   eval "$(scripts/detect-hardware.sh)"            # source variables into shell
#   scripts/detect-hardware.sh                       # print summary to stderr
#   scripts/detect-hardware.sh --dry-run             # show what would be pulled, don't pull
#   TIER=power scripts/detect-hardware.sh            # override auto-detection
#
# Tested hardware profiles:
#   - No GPU (CPU-only): tier=minimal, nomic-embed-text + llama3.2:1b
#   - Mid-range GPU (RTX 3060, 12 GB VRAM): tier=recommended, nomic-embed-text + llama3.2
#   - High-end GPU (RTX 4090, 24 GB VRAM): tier=power, nomic-embed-text + llama3.1:70b-q4

set -euo pipefail

DRY_RUN=false
for arg in "$@"; do
    case "$arg" in
        --dry-run) DRY_RUN=true ;;
        *) ;;
    esac
done

# ─── GPU detection ───
LG_GPU=false
LG_GPU_NAME="none"
LG_VRAM_MB=0

if ! command -v nvidia-smi &>/dev/null; then
    >&2 echo "  [info] nvidia-smi not found — assuming no NVIDIA GPU (CPU inference only)"
elif ! nvidia-smi &>/dev/null 2>&1; then
    >&2 echo "  [warn] nvidia-smi found but failed to query GPU — driver issue or no GPU present"
    >&2 echo "         Run 'nvidia-smi' manually to diagnose. Falling back to CPU tier."
else
    LG_GPU=true
    LG_GPU_NAME=$(nvidia-smi --query-gpu=name --format=csv,noheader,nounits 2>/dev/null | head -1 | xargs)
    LG_VRAM_MB=$(nvidia-smi --query-gpu=memory.total --format=csv,noheader,nounits 2>/dev/null | head -1 | xargs)
    if [[ -z "$LG_VRAM_MB" ]] || [[ "$LG_VRAM_MB" == "0" ]]; then
        >&2 echo "  [warn] GPU detected but VRAM query returned empty — using CPU tier as fallback"
        LG_GPU=false
        LG_GPU_NAME="none"
        LG_VRAM_MB=0
    fi
fi

# ─── System resources ───
if command -v free &>/dev/null; then
    LG_RAM_MB=$(free -m | awk '/^Mem:/{print $2}')
elif command -v sysctl &>/dev/null && sysctl -n hw.memsize &>/dev/null 2>&1; then
    LG_RAM_MB=$(( $(sysctl -n hw.memsize) / 1024 / 1024 ))
else
    >&2 echo "  [warn] Cannot determine RAM — defaulting to 0 MB"
    LG_RAM_MB=0
fi

PROJECT_DIR="${1:-.}"
# Strip --dry-run from path argument if passed first
[[ "$PROJECT_DIR" == "--dry-run" ]] && PROJECT_DIR="."

if command -v df &>/dev/null; then
    LG_DISK_FREE_GB=$(df -BG "$PROJECT_DIR" 2>/dev/null | awk 'NR==2{gsub(/G/,"",$4); print $4}' || echo 0)
else
    >&2 echo "  [warn] Cannot determine disk space — defaulting to 0 GB"
    LG_DISK_FREE_GB=0
fi

if command -v nproc &>/dev/null; then
    LG_CPU_CORES=$(nproc)
elif command -v sysctl &>/dev/null && sysctl -n hw.ncpu &>/dev/null 2>&1; then
    LG_CPU_CORES=$(sysctl -n hw.ncpu)
else
    LG_CPU_CORES=1
fi

# ─── Tier selection ───
if [[ -n "${TIER:-}" ]]; then
    LG_TIER="$TIER"
    >&2 echo "  [info] Tier override: TIER=${TIER}"
elif [[ "$LG_GPU" == "false" ]] || (( LG_VRAM_MB < 4000 )); then
    LG_TIER="minimal"
elif (( LG_VRAM_MB < 8000 )); then
    LG_TIER="standard"
elif (( LG_VRAM_MB < 16000 )); then
    LG_TIER="recommended"
else
    LG_TIER="power"
fi

# ─── Model selection per tier ───
case "$LG_TIER" in
    minimal)
        LG_EMBED_MODEL="nomic-embed-text"
        LG_LLM_MODEL="llama3.2:1b"
        LG_RERANKER_MODEL="BAAI/bge-reranker-base"
        ;;
    standard)
        LG_EMBED_MODEL="nomic-embed-text"
        LG_LLM_MODEL="llama3.2"
        LG_RERANKER_MODEL="BAAI/bge-reranker-base"
        ;;
    recommended)
        LG_EMBED_MODEL="nomic-embed-text"
        LG_LLM_MODEL="llama3.2"
        LG_RERANKER_MODEL="BAAI/bge-reranker-v2-m3"
        ;;
    power)
        LG_EMBED_MODEL="nomic-embed-text"
        LG_LLM_MODEL="llama3.1:70b-instruct-q4_K_M"
        LG_RERANKER_MODEL="BAAI/bge-reranker-v2-m3"
        ;;
    *)
        >&2 echo "  [error] Unknown tier '${LG_TIER}'. Valid values: minimal, standard, recommended, power"
        exit 1
        ;;
esac

# ─── Disk space warning ───
if (( LG_DISK_FREE_GB < 20 )); then
    >&2 echo ""
    >&2 echo "  [warn] Only ${LG_DISK_FREE_GB} GB free — minimum 20 GB recommended for models + data"
fi

# ─── Shell variable output ───
if [[ "$DRY_RUN" == "false" ]]; then
    cat << EOF
LG_GPU=$LG_GPU
LG_GPU_NAME="$LG_GPU_NAME"
LG_VRAM_MB=$LG_VRAM_MB
LG_RAM_MB=$LG_RAM_MB
LG_DISK_FREE_GB=$LG_DISK_FREE_GB
LG_CPU_CORES=$LG_CPU_CORES
LG_TIER=$LG_TIER
LG_EMBED_MODEL=$LG_EMBED_MODEL
LG_LLM_MODEL=$LG_LLM_MODEL
LG_RERANKER_MODEL=$LG_RERANKER_MODEL
EOF
fi

# ─── Human-readable summary ───
>&2 echo ""
>&2 echo "Hardware detected:"
if [[ "$LG_GPU" == "true" ]]; then
    >&2 echo "  GPU:  $LG_GPU_NAME ($((LG_VRAM_MB / 1024)) GB VRAM)"
else
    >&2 echo "  GPU:  None (CPU inference only)"
fi
>&2 echo "  RAM:  $((LG_RAM_MB / 1024)) GB"
>&2 echo "  Disk: ${LG_DISK_FREE_GB} GB free"
>&2 echo "  CPU:  $LG_CPU_CORES cores"
>&2 echo ""
>&2 echo "Model tier: $LG_TIER"
>&2 echo ""
>&2 echo "Models selected:"
>&2 echo "  Embedder:  $LG_EMBED_MODEL"
>&2 echo "  LLM:       $LG_LLM_MODEL"
>&2 echo "  Reranker:  $LG_RERANKER_MODEL (loaded from HuggingFace)"

if [[ "$DRY_RUN" == "true" ]]; then
    >&2 echo ""
    >&2 echo "Dry run — would pull the following Ollama models:"
    >&2 echo "  ollama pull $LG_EMBED_MODEL"
    >&2 echo "  ollama pull $LG_LLM_MODEL"
    >&2 echo ""
    >&2 echo "No models were pulled. Remove --dry-run to execute."
fi
