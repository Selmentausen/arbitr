#!/bin/bash
# ============================================================
# VPS Setup Script for Arbitr Scraper Workers
#
# Run on a fresh Ubuntu 24.04 Timeweb VPS:
#   curl -sSL https://raw.githubusercontent.com/YOUR_REPO/setup_vps.sh | bash
#   or: bash setup_vps.sh
#
# What it does:
#   1. Installs Python 3.12, pip, poetry
#   2. Clones the project (or you scp it)
#   3. Installs project dependencies
#   4. Installs Playwright + Chromium browser
#   5. Installs microsocks for IP binding
# ============================================================

set -e  # Exit on any error

echo "============================================"
echo "  Arbitr VPS Setup"
echo "============================================"

# --- 1. System dependencies ---
echo "[1/5] Installing system dependencies..."
apt-get update -qq
apt-get install -y -qq \
    python3 python3-pip python3-venv \
    git build-essential curl \
    # Playwright system deps (Chromium needs these)
    libnss3 libatk-bridge2.0-0 libdrm2 libxcomposite1 \
    libxdamage1 libxrandr2 libgbm1 libpango-1.0-0 \
    libcairo2 libasound2t64 libxshmfence1 \
    > /dev/null 2>&1

echo "    Python version: $(python3 --version)"

# --- 2. Install Poetry ---
echo "[2/5] Installing Poetry..."
if ! command -v poetry &> /dev/null; then
    curl -sSL https://install.python-poetry.org | python3 - > /dev/null 2>&1
    export PATH="$HOME/.local/bin:$PATH"
    echo 'export PATH="$HOME/.local/bin:$PATH"' >> ~/.bashrc
fi
echo "    Poetry version: $(poetry --version)"

# --- 3. Project setup ---
echo "[3/5] Setting up project..."
PROJECT_DIR="/opt/arbitr"

if [ -d "$PROJECT_DIR" ]; then
    echo "    Project directory exists, pulling latest..."
    cd "$PROJECT_DIR"
    # If it's a git repo, pull. Otherwise skip.
    if [ -d ".git" ]; then
        git pull
    fi
else
    echo "    NOTE: Project not found at $PROJECT_DIR"
    echo "    You need to copy the project there. Options:"
    echo "      a) git clone YOUR_REPO $PROJECT_DIR"
    echo "      b) scp -r /local/path/to/Arbitr root@VPS_IP:$PROJECT_DIR"
    echo ""
    echo "    After copying, re-run this script."
    mkdir -p "$PROJECT_DIR"
fi

if [ -f "$PROJECT_DIR/pyproject.toml" ]; then
    cd "$PROJECT_DIR"
    
    # Configure poetry to create venv in project directory
    poetry config virtualenvs.in-project true
    
    echo "    Installing Python dependencies..."
    poetry install --no-interaction --quiet 2>&1 | tail -3
    
    # --- 4. Playwright browsers ---
    echo "[4/5] Installing Playwright Chromium..."
    poetry run playwright install chromium > /dev/null 2>&1
    poetry run playwright install-deps chromium > /dev/null 2>&1 || true
    echo "    Playwright installed"
else
    echo "[4/5] Skipping dependency install (no pyproject.toml found)"
fi

# --- 5. Microsocks ---
echo "[5/5] Installing microsocks..."
if ! command -v microsocks &> /dev/null; then
    git clone https://github.com/rofl0r/microsocks.git /tmp/microsocks > /dev/null 2>&1
    cd /tmp/microsocks && make > /dev/null 2>&1
    cp microsocks /usr/local/bin/
    rm -rf /tmp/microsocks
fi
echo "    microsocks installed: $(which microsocks)"

echo ""
echo "============================================"
echo "  Setup complete!"
echo ""
echo "  Next steps:"
echo "  1. Copy project to $PROJECT_DIR (if not done)"
echo "  2. Add additional IPs:  ip addr add X.X.X.X/24 dev eth0"
echo "  3. Start proxies:       microsocks -i 127.0.0.1 -p 10001 -b MAIN_IP > /dev/null 2>&1 &"
echo "  4. Start workers:       See README"
echo "============================================"
