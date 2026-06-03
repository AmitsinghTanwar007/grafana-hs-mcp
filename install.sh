#!/usr/bin/env bash
set -euo pipefail

REPO_URL="git+https://github.com/AmitsinghTanwar007/grafana-hs-mcp.git"
APP_HOME="${GRAFANA_HS_MCP_INSTALL_HOME:-$HOME/.grafana-hs-mcp/app}"
VENV_DIR="$APP_HOME/venv"

step() {
  echo
  echo "[$1/5] $2"
}

pick_bin_dir() {
  if [ -n "${GRAFANA_HS_MCP_BIN_DIR:-}" ]; then
    echo "$GRAFANA_HS_MCP_BIN_DIR"
    return
  fi

  case ":$PATH:" in
    *":$HOME/.local/bin:"*) echo "$HOME/.local/bin"; return ;;
    *":$HOME/bin:"*) echo "$HOME/bin"; return ;;
    *":/usr/local/bin:"*) [ -w /usr/local/bin ] && echo "/usr/local/bin" && return ;;
    *":/opt/homebrew/bin:"*) [ -w /opt/homebrew/bin ] && echo "/opt/homebrew/bin" && return ;;
  esac

  echo "$HOME/.local/bin"
}

ensure_path_hint() {
  case ":$PATH:" in
    *":$BIN_DIR:"*) return 0 ;;
  esac

  shell_name="$(basename "${SHELL:-}")"
  case "$shell_name" in
    zsh) rc_file="$HOME/.zshrc" ;;
    bash) rc_file="$HOME/.bashrc" ;;
    *) rc_file="$HOME/.profile" ;;
  esac

  path_line="export PATH=\"$BIN_DIR:\$PATH\""
  if [ ! -f "$rc_file" ] || ! grep -Fq "$path_line" "$rc_file"; then
    printf '\n# grafana-hs-mcp\n%s\n' "$path_line" >> "$rc_file"
  fi

  echo "$BIN_DIR was added to $rc_file."
  echo "Open a new terminal, or run:"
  echo "  export PATH=\"$BIN_DIR:\$PATH\""
  return 1
}

print_welcome() {
  echo
  echo "grafana-hs-mcp installed successfully"
  echo
  echo "Available commands:"
  echo "  grafana-hs-mcp setup             Configure Grafana login"
  echo "  grafana-hs-mcp doctor            Verify Grafana access"
  echo "  grafana-hs-mcp env               Show config values"
  echo "  grafana-hs-mcp env --interactive Edit config values"
  echo "  grafana-hs-mcp update            Update to the latest version"
  echo
  echo "Next step:"
  echo "  grafana-hs-mcp setup"
  echo
}

echo
echo "Installing grafana-hs-mcp..."

step 1 "Checking python3"
if ! command -v python3 >/dev/null 2>&1; then
  echo "python3 is required but was not found."
  exit 1
fi

step 2 "Creating isolated environment"
BIN_DIR="$(pick_bin_dir)"
mkdir -p "$APP_HOME" "$BIN_DIR"

if [ ! -d "$VENV_DIR" ]; then
  if ! python3 -m venv "$VENV_DIR"; then
    echo
    echo "Could not create a Python virtual environment."
    echo "On Ubuntu/Debian, install venv support with: sudo apt install python3-venv"
    exit 1
  fi
fi

step 3 "Installing grafana-hs-mcp"
"$VENV_DIR/bin/python" -m pip install --upgrade pip >/dev/null
"$VENV_DIR/bin/python" -m pip install --upgrade --force-reinstall --no-cache-dir "$REPO_URL"

step 4 "Creating command"
ln -sf "$VENV_DIR/bin/grafana-hs-mcp" "$BIN_DIR/grafana-hs-mcp"

step 5 "Ready"
echo "Installed grafana-hs-mcp"
echo "Binary: $BIN_DIR/grafana-hs-mcp"
echo
ensure_path_hint || true
print_welcome
