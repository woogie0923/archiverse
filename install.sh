#!/usr/bin/env sh
set -eu

# Archiverse setup (macOS/Linux)
# - Installs uv if missing
# - Runs uv sync
# - Creates config.yaml from template if missing

cd "$(dirname "$0")"

echo ""
echo "=== Archiverse setup (macOS/Linux) ==="
echo ""

if command -v uv >/dev/null 2>&1; then
  echo "[OK] uv is already installed."
else
  echo "[..] uv not found. Installing..."
  # Official installer: https://docs.astral.sh/uv/getting-started/installation/
  if command -v curl >/dev/null 2>&1; then
    curl -LsSf https://astral.sh/uv/install.sh | sh
  elif command -v wget >/dev/null 2>&1; then
    wget -qO- https://astral.sh/uv/install.sh | sh
  else
    echo "[ERR] Neither curl nor wget found. Install uv manually:"
    echo "      https://docs.astral.sh/uv/getting-started/installation/"
    exit 1
  fi

  # Common install location is ~/.local/bin; make it available for this script run.
  if [ -x "$HOME/.local/bin/uv" ]; then
    PATH="$HOME/.local/bin:$PATH"
    export PATH
  fi

  # Verify
  if ! command -v uv >/dev/null 2>&1; then
    echo "[ERR] uv still not reachable in this shell. Open a new terminal and re-run this script."
    exit 1
  fi
  echo "[OK] uv installed and reachable."
fi

echo ""
uv sync

if [ ! -f "config.yaml" ]; then
  cp "config.yaml.template" "config.yaml"
  echo "[OK] Created config.yaml from template."
fi

echo ""
echo "Installation completed successfully!"
echo "Try:"
echo "  uv run archiverse --help"
echo ""

