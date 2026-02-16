#!/usr/bin/env bash
# Fluxer Music Bot - Linux/macOS Setup Script
# This script installs all dependencies automatically.

set -e

echo "============================================"
echo "  Fluxer Music Bot - Setup (Linux/macOS)"
echo "============================================"
echo ""

# Detect OS and architecture
OS="$(uname -s)"
ARCH="$(uname -m)"

# 1. Python dependencies
echo "[1/3] Installing Python dependencies..."
pip install -r requirements.txt
echo "      Done."
echo ""

# 2. Deno (JS runtime for yt-dlp signature solving)
echo "[2/3] Installing Deno (JS runtime)..."
if command -v deno &>/dev/null; then
    echo "      Deno already installed ($(deno --version | head -1))."
else
    curl -fsSL https://deno.land/install.sh | sh
    # Add to PATH for this session
    export PATH="$HOME/.deno/bin:$PATH"
    echo "      Done. Added ~/.deno/bin to PATH."
fi
echo ""

# 3. bgutil-pot (YouTube PO token generator)
echo "[3/3] Downloading bgutil-pot (PO token generator)..."
if [ -f "./bgutil-pot" ]; then
    echo "      bgutil-pot already exists, skipping download."
else
    # Determine the right binary for this platform
    case "$OS" in
        Linux)
            BGUTIL_URL="https://github.com/jim60105/bgutil-ytdlp-pot-provider-rs/releases/latest/download/bgutil-pot-linux-x86_64"
            ;;
        Darwin)
            if [ "$ARCH" = "arm64" ]; then
                BGUTIL_URL="https://github.com/jim60105/bgutil-ytdlp-pot-provider-rs/releases/latest/download/bgutil-pot-macos-aarch64"
            else
                BGUTIL_URL="https://github.com/jim60105/bgutil-ytdlp-pot-provider-rs/releases/latest/download/bgutil-pot-macos-x86_64"
            fi
            ;;
        *)
            echo "ERROR: Unsupported OS: $OS"
            echo "       Download manually from: https://github.com/jim60105/bgutil-ytdlp-pot-provider-rs/releases"
            exit 1
            ;;
    esac

    curl -fsSL "$BGUTIL_URL" -o bgutil-pot
    chmod +x bgutil-pot
    echo "      Done."
fi
echo ""

# 4. Config check
if [ ! -f "config.yaml" ]; then
    echo "[!] config.yaml not found. Creating from template..."
    if [ -f "config.example.yaml" ]; then
        cp config.example.yaml config.yaml
        echo "    Created config.yaml - edit it with your bot token before running!"
    else
        echo "    WARNING: config.example.yaml not found. Create config.yaml manually."
    fi
fi
echo ""

echo "============================================"
echo "  Setup complete!"
echo ""
echo "  Next steps:"
echo "  1. Edit config.yaml with your bot token"
echo "  2. Run: python main.py"
echo "============================================"
