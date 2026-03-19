#!/usr/bin/env bash
# Lumogis first-run setup.
#
# Detects hardware, generates model configs, starts services, pulls models.
# Idempotent — safe to re-run after a GPU upgrade or to change tiers.
#
# Usage:
#   make setup               # auto-detect hardware
#   make setup TIER=power    # override tier
#   scripts/setup.sh --dry-run  # show what would happen without changing anything

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
BOLD='\033[1m'
NC='\033[0m'

info()   { echo -e "  ${GREEN}✓${NC} $1"; }
warn()   { echo -e "  ${YELLOW}!${NC} $1"; }
err()    { echo -e "  ${RED}✗${NC} $1"; }
header() { echo -e "\n${BOLD}$1${NC}"; }

DRY_RUN=false
for arg in "$@"; do
    [[ "$arg" == "--dry-run" ]] && DRY_RUN=true
done

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Step 1: Detect hardware
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
header "Step 1/5: Detecting hardware..."
eval "$("$SCRIPT_DIR/detect-hardware.sh" "$PROJECT_DIR")"

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Step 2: Determine models for tier
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
header "Step 2/5: Selecting models for tier '$LG_TIER'..."

OLLAMA_MODELS=()
LITELLM_NAMES=()
LIBRECHAT_MODELS=()

case "$LG_TIER" in
    minimal)
        OLLAMA_MODELS=("llama3.2:1b" "nomic-embed-text")
        LITELLM_NAMES=("llama")
        LLAMA_OLLAMA="llama3.2:1b"
        LLAMA_LABEL="Llama 3.2 (Local)"
        LLAMA_DESC="Lightweight model for CPU inference — good for quick questions and drafting. Runs without a GPU."
        LLAMA_PARAMS="1B"
        LLAMA_PREFIX="You are Llama 3.2, a 1-billion-parameter language model running locally via Ollama. You are part of Lumogis, a local-first AI assistant. You run entirely on the user's machine — no data leaves their device. Never identify yourself as LLaMA 2 or any other model."
        LLAMA_GREETING="You are connected to Llama 3.2 (1B) running locally on your CPU via Ollama.\nEverything stays on your machine — nothing is sent to any external service.\nI'm a lightweight model — good for quick questions and drafting. For complex research, switch to Claude."
        ;;
    standard)
        OLLAMA_MODELS=("llama3.2:3b" "nomic-embed-text")
        LITELLM_NAMES=("llama")
        LLAMA_OLLAMA="llama3.2:3b"
        LLAMA_LABEL="Llama 3.2 (Local)"
        LLAMA_DESC="Lightweight and fast — best for quick questions, brainstorming, and drafting. Runs entirely on your GPU. Nothing leaves your machine."
        LLAMA_PARAMS="3B"
        LLAMA_PREFIX="You are Llama 3.2, a 3-billion-parameter language model running locally via Ollama. You are part of Lumogis, a local-first AI assistant. You run entirely on the user's machine — no data leaves their device. Never identify yourself as LLaMA 2 or any other model."
        LLAMA_GREETING="You are connected to Llama 3.2 (3B) running locally on your GPU via Ollama.\nEverything stays on your machine — nothing is sent to any external service.\nI'm the fastest option — good for quick questions, brainstorming, and drafting. For complex research, switch to Claude."
        ;;
    recommended|power)
        OLLAMA_MODELS=("qwen2.5:7b" "qwen2.5-coder:7b" "llama3.2:3b" "nomic-embed-text")
        LITELLM_NAMES=("qwen" "qwen-coder" "llama")
        LLAMA_OLLAMA="llama3.2:3b"
        LLAMA_LABEL="Llama 3.2 (Local)"
        LLAMA_DESC="Lightweight and fast — best for quick questions, brainstorming, and drafting. Smallest model, fastest responses. Runs entirely on your GPU. Nothing leaves your machine."
        LLAMA_PARAMS="3B"
        LLAMA_PREFIX="You are Llama 3.2, a 3-billion-parameter language model running locally via Ollama. You are part of Lumogis, a local-first AI assistant. You run entirely on the user's machine — no data leaves their device. Never identify yourself as LLaMA 2 or any other model."
        LLAMA_GREETING="You are connected to Llama 3.2 (3B) running locally on your GPU via Ollama.\nEverything stays on your machine — nothing is sent to any external service.\nI'm the fastest option — good for quick questions, brainstorming, and drafting. For complex research, switch to Claude."
        ;;
esac

echo ""
for m in "${OLLAMA_MODELS[@]}"; do
    info "$m"
done

if [[ "$DRY_RUN" == "true" ]]; then
    header "Dry run — showing what would be generated (no files changed)"
    echo ""
    echo "  Tier:   $LG_TIER"
    echo "  Models: ${OLLAMA_MODELS[*]}"
    echo "  GPU:    $LG_GPU ($LG_GPU_NAME)"
    echo ""
    echo "  Would generate:"
    echo "    config/models.yaml"
    echo "    config/litellm.yaml (optional)"
    echo "    config/librechat.yaml"
    if [[ "$LG_GPU" == "true" ]]; then
        echo "    COMPOSE_FILE=docker-compose.yml:docker-compose.gpu.yml (in .env)"
    else
        echo "    COMPOSE_FILE=docker-compose.yml (in .env, no GPU)"
    fi
    echo ""
    echo "  Would pull Ollama models: ${OLLAMA_MODELS[*]}"
    exit 0
fi

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Step 3: Generate configs
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
header "Step 3/5: Generating configs..."

# --- .env ---
if [[ ! -f "$PROJECT_DIR/.env" ]]; then
    cp "$PROJECT_DIR/.env.example" "$PROJECT_DIR/.env"
    info "Created .env from .env.example"
    warn "Edit .env to set ANTHROPIC_API_KEY and FILESYSTEM_ROOT"
else
    info ".env already exists"
fi

# Auto-generate JWT secrets if still set to the placeholder value
_gen_secret() {
    if command -v openssl &>/dev/null; then
        openssl rand -hex 32
    else
        python3 -c "import secrets; print(secrets.token_hex(32))"
    fi
}
if grep -q "^JWT_SECRET=change-me-in-production" "$PROJECT_DIR/.env" 2>/dev/null; then
    sed -i "s|^JWT_SECRET=change-me-in-production|JWT_SECRET=$(_gen_secret)|" "$PROJECT_DIR/.env"
    info "Generated JWT_SECRET"
fi
if grep -q "^JWT_REFRESH_SECRET=change-me-in-production" "$PROJECT_DIR/.env" 2>/dev/null; then
    sed -i "s|^JWT_REFRESH_SECRET=change-me-in-production|JWT_REFRESH_SECRET=$(_gen_secret)|" "$PROJECT_DIR/.env"
    info "Generated JWT_REFRESH_SECRET"
fi

# Set COMPOSE_FILE in .env
if [[ "$LG_GPU" == "true" ]]; then
    COMPOSE_VALUE="docker-compose.yml:docker-compose.gpu.yml"
else
    COMPOSE_VALUE="docker-compose.yml"
    warn "No GPU detected — Ollama will use CPU inference (slower)"
fi

if grep -q "^COMPOSE_FILE=" "$PROJECT_DIR/.env" 2>/dev/null; then
    sed -i "s|^COMPOSE_FILE=.*|COMPOSE_FILE=$COMPOSE_VALUE|" "$PROJECT_DIR/.env"
else
    echo "" >> "$PROJECT_DIR/.env"
    echo "# Docker Compose file selection (set by make setup)" >> "$PROJECT_DIR/.env"
    echo "COMPOSE_FILE=$COMPOSE_VALUE" >> "$PROJECT_DIR/.env"
fi
info "COMPOSE_FILE=$COMPOSE_VALUE"

# --- config/litellm.yaml ---
{
    cat << 'LITELLM_HEADER'
model_list:
  - model_name: claude
    litellm_params:
      model: anthropic/claude-sonnet-4-20250514
      api_key: os.environ/ANTHROPIC_API_KEY

LITELLM_HEADER
    for name in "${LITELLM_NAMES[@]}"; do
        case "$name" in
            qwen)
                cat << 'EOF'
  - model_name: qwen
    litellm_params:
      model: ollama/qwen2.5:7b
      api_base: http://ollama:11434
EOF
                ;;
            qwen-coder)
                cat << 'EOF'
  - model_name: qwen-coder
    litellm_params:
      model: ollama/qwen2.5-coder:7b
      api_base: http://ollama:11434
EOF
                ;;
            llama)
                cat << EOF
  - model_name: llama
    litellm_params:
      model: ollama/$LLAMA_OLLAMA
      api_base: http://ollama:11434
EOF
                ;;
        esac
    done

    cat << 'LITELLM_FOOTER'

router_settings:
  routing_strategy: simple-shuffle

litellm_settings:
  drop_params: true
  timeout: 120
LITELLM_FOOTER
} > "$PROJECT_DIR/config/litellm.yaml"
info "Generated config/litellm.yaml (optional — for rate limiting/observability)"

# --- config/models.yaml ---
{
    cat << 'MODELS_HEADER'
models:
  claude:
    adapter: anthropic
    model: claude-sonnet-4-20250514
    api_key_env: ANTHROPIC_API_KEY
    tools: true
    # proxy_url: http://litellm:4000  # uncomment to route through LiteLLM

MODELS_HEADER
    for name in "${LITELLM_NAMES[@]}"; do
        case "$name" in
            qwen)
                cat << 'EOF'
  qwen:
    adapter: openai
    model: qwen2.5:7b
    base_url: http://ollama:11434/v1
    tools: true
    # proxy_url: http://litellm:4000

EOF
                ;;
            qwen-coder)
                cat << 'EOF'
  qwen-coder:
    adapter: openai
    model: qwen2.5-coder:7b
    base_url: http://ollama:11434/v1
    tools: false

EOF
                ;;
            llama)
                cat << EOF
  llama:
    adapter: openai
    model: $LLAMA_OLLAMA
    base_url: http://ollama:11434/v1
    tools: false

EOF
                ;;
        esac
    done

    cat << 'MODELS_FOOTER'
  # --- Examples: uncomment and set API keys in .env ---
  # chatgpt:
  #   adapter: openai
  #   model: gpt-4o
  #   api_key_env: OPENAI_API_KEY
  #   tools: true
  #
  # perplexity:
  #   adapter: openai
  #   model: sonar-pro
  #   api_key_env: PERPLEXITY_API_KEY
  #   base_url: https://api.perplexity.ai
  #   tools: false
MODELS_FOOTER
} > "$PROJECT_DIR/config/models.yaml"
info "Generated config/models.yaml"

# --- config/librechat.yaml ---
# Build the model list for the endpoint
MODEL_LIST="\"claude\""
for name in "${LITELLM_NAMES[@]}"; do
    MODEL_LIST="$MODEL_LIST, \"$name\""
done

{
    cat << EOF
version: 1.3.5
cache: true

endpoints:
  custom:
    - name: "Lumogis"
      apiKey: "ignored"
      baseURL: "http://orchestrator:8000/v1"
      models:
        default: [$MODEL_LIST]
        fetch: false
      titleConvo: false
      summarize: false
      forcePrompt: false
      modelDisplayLabel: "Lumogis"

modelSpecs:
  enforce: true
  prioritize: true
  list:
    - name: "claude"
      label: "Claude (Cloud)"
      default: true
      description: >
        Best for complex questions, research, and file search.
        Can search your indexed files by meaning and read their contents.
        Your files stay on your machine — only the question and relevant
        context snippets are sent to Claude via your own API key.
      iconURL: "anthropic"
      preset:
        endpoint: "Lumogis"
        model: "claude"
        modelLabel: "Claude (Cloud)"
        greeting: |
          You are connected to Claude via your own Anthropic API key.
          Your files stay on your machine — only the question and relevant context snippets are sent to Claude. No files are uploaded or stored externally.
          I can search your indexed files by meaning and read their contents. Best for complex questions, research, and anything that benefits from tool use.
EOF

    # Qwen 2.5 (only for recommended/power)
    for name in "${LITELLM_NAMES[@]}"; do
        case "$name" in
            qwen)
                cat << 'EOF'
    - name: "qwen"
      label: "Qwen 2.5 (Local)"
      description: >
        Smart local model with file search — can search your indexed files
        by meaning and read their contents, all running on your GPU.
        Good for reasoning, analysis, and private research.
        Nothing leaves your machine.
      iconURL: "ollama"
      preset:
        endpoint: "Lumogis"
        model: "qwen"
        modelLabel: "Qwen 2.5 (Local)"
        promptPrefix: "You are Qwen 2.5, a 7-billion-parameter language model running locally via Ollama. You are part of Lumogis, a local-first AI assistant. You can search and read the user's local files. You run entirely on the user's machine — no data leaves their device."
        greeting: |
          You are connected to Qwen 2.5 (7B) running locally on your GPU via Ollama.
          Everything stays on your machine — nothing is sent to any external service.
          I can search your indexed files by meaning and read their contents — fully private file search with zero data leaving your machine.
EOF
                ;;
            qwen-coder)
                cat << 'EOF'
    - name: "qwen-coder"
      label: "Qwen 2.5 Coder (Local)"
      description: >
        Specialised coding model — code generation, debugging, code review,
        and technical explanations. Runs entirely on your GPU.
        Nothing leaves your machine.
      iconURL: "ollama"
      preset:
        endpoint: "Lumogis"
        model: "qwen-coder"
        modelLabel: "Qwen 2.5 Coder (Local)"
        promptPrefix: "You are Qwen 2.5 Coder, a 7-billion-parameter coding model running locally via Ollama. You are part of Lumogis, a local-first AI assistant. You specialise in code generation, debugging, and code review. You run entirely on the user's machine — no data leaves their device."
        greeting: |
          You are connected to Qwen 2.5 Coder (7B) running locally on your GPU via Ollama.
          Everything stays on your machine — nothing is sent to any external service.
          I'm specialised for coding: generation, debugging, review, and technical explanations.
EOF
                ;;
            llama)
                cat << EOF
    - name: "llama"
      label: "$LLAMA_LABEL"
      description: >
        $LLAMA_DESC
      iconURL: "ollama"
      preset:
        endpoint: "Lumogis"
        model: "llama"
        modelLabel: "$LLAMA_LABEL"
        promptPrefix: "$LLAMA_PREFIX"
EOF
                # Greeting needs careful formatting — write each line explicitly
                echo "        greeting: |"
                echo -e "$LLAMA_GREETING" | while IFS= read -r line; do
                    echo "          $line"
                done
                ;;
        esac
    done
} > "$PROJECT_DIR/config/librechat.yaml"
info "Generated config/librechat.yaml"

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Step 4: Start services
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
header "Step 4/5: Starting services..."

cd "$PROJECT_DIR"
# Use || true so that port-already-in-use errors (e.g. re-running setup on a
# machine with an existing stack) do not abort the script before model pulls.
docker compose up -d --build 2>&1 | tail -8 || {
    warn "docker compose up exited non-zero — some services may already be running."
    warn "This is normal if you are re-running setup. Services that were already up are fine."
}
info "Services started (or already running)"

# Wait for Ollama to be ready
echo -n "  Waiting for Ollama"
for i in $(seq 1 30); do
    if docker compose exec -T ollama ollama list &>/dev/null; then
        echo ""
        info "Ollama ready"
        break
    fi
    echo -n "."
    sleep 2
done

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Step 5: Pull models
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
header "Step 5/5: Pulling models (this may take a few minutes on first run)..."

for model in "${OLLAMA_MODELS[@]}"; do
    echo -ne "  Pulling ${BOLD}$model${NC}... "
    if docker compose exec -T ollama ollama pull "$model" &>/dev/null; then
        echo -e "${GREEN}done${NC}"
    else
        echo -e "${RED}failed${NC}"
        err "Could not pull $model — check docker compose logs ollama"
    fi
done

# Restart orchestrator + librechat to pick up new configs
docker compose restart orchestrator librechat &>/dev/null
info "Restarted orchestrator + librechat with new configs"

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Summary
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
header "Setup complete!"
echo ""
echo -e "  Tier: ${BOLD}$LG_TIER${NC}"
if [[ "$LG_GPU" == "true" ]]; then
    echo -e "  GPU:  $LG_GPU_NAME ($((LG_VRAM_MB / 1024)) GB VRAM)"
fi
echo ""
echo "  Models available:"
echo -e "    ${GREEN}✓${NC} Claude (Cloud)  — complex research, file search"
for name in "${LITELLM_NAMES[@]}"; do
    case "$name" in
        qwen)       echo -e "    ${GREEN}✓${NC} Qwen 2.5 (Local)       — file search, reasoning, fully private" ;;
        qwen-coder) echo -e "    ${GREEN}✓${NC} Qwen 2.5 Coder (Local) — code generation, debugging" ;;
        llama)      echo -e "    ${GREEN}✓${NC} Llama 3.2 (Local)      — quick chat, brainstorming, fastest" ;;
    esac
done
echo ""
echo -e "  Open ${BOLD}http://localhost:3080${NC} to start using Lumogis."
echo ""
if ! grep -q "^ANTHROPIC_API_KEY=." "$PROJECT_DIR/.env" 2>/dev/null; then
    warn "Set ANTHROPIC_API_KEY in .env to enable Claude (cloud model)."
fi
if ! grep -q "^FILESYSTEM_ROOT=." "$PROJECT_DIR/.env" 2>/dev/null; then
    warn "Set FILESYSTEM_ROOT in .env to the folder you want Lumogis to index."
fi
