#!/usr/bin/env bash
set -e

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_DIR="$DIR/.venv"

# 1. Validate that Python 3 is installed
if ! command -v python3 &>/dev/null; then
    echo "❌ Error: Python 3 is not installed or not available in PATH."
    echo ""
    echo "👉 Please install Python 3 (Python 3.11+ recommended):"
    echo "   - Ubuntu/Debian: sudo apt update && sudo apt install -y python3 python3-venv python3-pip"
    echo "   - macOS:         brew install python3"
    echo "   - Fedora/RHEL:   sudo dnf install -y python3 python3-pip"
    exit 1
fi

# 2. Automatically create virtual environment if not present
if [ ! -d "$VENV_DIR" ]; then
    echo "📦 Virtual environment not found. Creating .venv..."
    if ! python3 -m venv "$VENV_DIR" 2>/dev/null; then
        echo "❌ Error: Failed to create virtual environment with 'python3 -m venv'."
        echo "👉 If you are on Ubuntu/Debian, install the venv package:"
        echo "   sudo apt install -y python3-venv"
        exit 1
    fi
    echo "📥 Installing required dependencies from requirements.txt..."
    "$VENV_DIR/bin/pip" install --upgrade pip >/dev/null 2>&1 || true
    "$VENV_DIR/bin/pip" install -r "$DIR/requirements.txt"
    echo "✅ Dependencies installed successfully!"
fi

PYTHON_BIN="$VENV_DIR/bin/python3"
exec "$PYTHON_BIN" "$DIR/simulate_pr.py" "$@"
