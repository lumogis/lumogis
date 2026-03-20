#!/usr/bin/env bash
# Lumogis installer.
#
# Downloads and installs Lumogis in one command:
#
#   curl -fsSL https://raw.githubusercontent.com/lumogis/lumogis/main/scripts/install.sh | bash
#
# Or with a custom install path:
#
#   curl -fsSL https://raw.githubusercontent.com/lumogis/lumogis/main/scripts/install.sh | bash -s -- --dir ~/mypath
#
# On Windows: run inside WSL2, not PowerShell or CMD.

set -euo pipefail

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
BOLD='\033[1m'
NC='\033[0m'

info()   { echo -e "  ${GREEN}✓${NC} $1"; }
warn()   { echo -e "  ${YELLOW}!${NC} $1"; }
err()    { echo -e "  ${RED}✗${NC} $1"; }
header() { echo -e "\n${BOLD}$1${NC}"; }

REPO_URL="https://github.com/lumogis/lumogis.git"
DEFAULT_DIR="$HOME/lumogis"
INSTALL_DIR=""

# Parse --dir argument
while [[ $# -gt 0 ]]; do
    case "$1" in
        --dir)   INSTALL_DIR="$2"; shift ;;
        --dir=*) INSTALL_DIR="${1#--dir=}" ;;
    esac
    shift
done

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Check dependencies
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
header "Checking dependencies..."

_require() {
    if ! command -v "$1" &>/dev/null; then
        err "$1 is not installed. $2"
        exit 1
    fi
    info "$1 found"
}

_require git  "Install git: https://git-scm.com"
_require docker "Install Docker Desktop: https://docs.docker.com/desktop/"
_require make "Install make (Linux: sudo apt install make  |  macOS: xcode-select --install)"

# Warn if running on a Windows-mounted path (WSL /mnt/c etc.)
if [[ "$(pwd)" == /mnt/* ]]; then
    warn "You are in a Windows-mounted directory (/mnt/...)."
    warn "Lumogis must be installed inside the WSL filesystem for correct permissions."
    warn "Defaulting install path to $DEFAULT_DIR"
fi

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Choose install directory
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
header "Where do you want to install Lumogis?"

if [[ -z "$INSTALL_DIR" ]]; then
    echo -e "  Press Enter to accept the default, or type a path."
    echo -n "  [$DEFAULT_DIR]: "
    read -r INSTALL_DIR
    [[ -z "$INSTALL_DIR" ]] && INSTALL_DIR="$DEFAULT_DIR"
fi

# Expand ~ manually
INSTALL_DIR="${INSTALL_DIR/#\~/$HOME}"

# Refuse Windows-mounted paths
if [[ "$INSTALL_DIR" == /mnt/* ]]; then
    err "Cannot install to a Windows-mounted path ($INSTALL_DIR)."
    err "Choose a path inside the WSL filesystem, e.g. $DEFAULT_DIR"
    exit 1
fi

if [[ -d "$INSTALL_DIR/.git" ]]; then
    err "$INSTALL_DIR already contains a git repository."
    err "Choose a different path or delete the existing directory first."
    exit 1
fi

info "Install path: $INSTALL_DIR"

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Clone
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
header "Cloning Lumogis..."

git clone "$REPO_URL" "$INSTALL_DIR"
info "Cloned to $INSTALL_DIR"

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Copy .env
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
cp "$INSTALL_DIR/.env.example" "$INSTALL_DIR/.env"
info "Created .env"

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Run setup
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
cd "$INSTALL_DIR"
bash scripts/setup.sh
