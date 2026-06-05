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
  echo "  grafana-hs-mcp configure-all     Add MCP config to supported AI clients"
  echo "  grafana-hs-mcp configure-claude-code Add MCP config to Claude Code"
  echo "  grafana-hs-mcp env               Show config values"
  echo "  grafana-hs-mcp env --interactive Edit config values"
  echo "  grafana-hs-mcp update            Update to the latest version"
  echo "  grafana-hs-mcp cleanup           Remove local files"
  echo "  grafana-hs-mcp cleanup --browser-cache Remove local files + browser cache"
  echo
  echo "Next step:"
  echo "  grafana-hs-mcp setup"
  echo "  grafana-hs-mcp configure-all"
  echo
}

echo
echo "Installing grafana-hs-mcp..."

step 1 "Checking python3 (>=3.10 required)"

PYTHON=""
for candidate in python3.14 python3.13 python3.12 python3.11 python3.10 python3 python; do
  if command -v "$candidate" >/dev/null 2>&1; then
    version="$("$candidate" -c 'import sys; print("%d.%d" % sys.version_info[:2])' 2>/dev/null || true)"
    major="${version%%.*}"
    minor="${version##*.}"
    if [ -n "$major" ] && [ "$major" -gt 3 ] 2>/dev/null; then
      PYTHON="$candidate"; break
    elif [ -n "$major" ] && [ "$major" -eq 3 ] && [ "$minor" -ge 10 ] 2>/dev/null; then
      PYTHON="$candidate"; break
    fi
  fi
done

if [ -z "$PYTHON" ]; then
  echo
  echo "Python 3.10 or later is required but was not found."
  echo
  echo "  macOS:   brew install python@3.12"
  echo "  Ubuntu:  sudo apt install python3.12"
  echo "  Or download from https://www.python.org/downloads/"
  echo
  echo "Your current python3 version:"
  python3 --version 2>/dev/null || echo "  (python3 not found)"
  exit 1
fi

echo "Using $PYTHON ($("$PYTHON" --version))"

step 2 "Creating isolated environment"
BIN_DIR="$(pick_bin_dir)"
mkdir -p "$APP_HOME" "$BIN_DIR"

if [ ! -d "$VENV_DIR" ]; then
  if ! "$PYTHON" -m venv "$VENV_DIR"; then
    echo
    echo "Could not create a Python virtual environment."
    echo "On Ubuntu/Debian: sudo apt install python3.12 python3.12-venv"
    exit 1
  fi
fi

step 3 "Installing grafana-hs-mcp"
"$VENV_DIR/bin/python" -m pip install --upgrade pip setuptools wheel >/dev/null
"$VENV_DIR/bin/python" -m pip install --upgrade "$REPO_URL"

step 4 "Creating command"
ln -sf "$VENV_DIR/bin/grafana-hs-mcp" "$BIN_DIR/grafana-hs-mcp"

step 5 "Ready"
echo "Installed grafana-hs-mcp"
echo "Binary: $BIN_DIR/grafana-hs-mcp"
echo
ensure_path_hint || true
print_welcome
