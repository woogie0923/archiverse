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

  # Try common install locations for this script run.
  UV_BIN=""
  for d in "$HOME/.local/bin" "$HOME/.cargo/bin" "/usr/local/bin"; do
    if [ -x "$d/uv" ]; then
      UV_BIN="$d"
      break
    fi
  done
  if [ -n "$UV_BIN" ]; then
    PATH="$UV_BIN:$PATH"
    export PATH
  fi

  # Verify
  if ! command -v uv >/dev/null 2>&1; then
    echo "[ERR] uv installed but is not on PATH in this shell."
    echo ""
    echo "Add uv to PATH, then reopen your terminal:"
    echo "  export PATH=\"\$HOME/.local/bin:\$PATH\""
    echo ""
    echo "To persist on Linux/macOS, add to your shell profile (e.g. ~/.bashrc, ~/.zshrc):"
    echo "  export PATH=\"\$HOME/.local/bin:\$PATH\""
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
echo "If 'uv: command not found' appears in another terminal, add:"
echo "  export PATH=\"\$HOME/.local/bin:\$PATH\""
echo "to your shell profile (e.g. ~/.bashrc or ~/.zshrc), then restart the shell."
echo ""

