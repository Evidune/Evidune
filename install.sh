#!/bin/sh

set -eu

REPO_GH="${EVIDUNE_REPO_GH:-Evidune/Evidune}"
REPO_SSH="${EVIDUNE_REPO_SSH:-git@github.com:Evidune/Evidune.git}"
INSTALL_ROOT="${EVIDUNE_HOME:-$HOME/.evidune}"
SRC_DIR="${EVIDUNE_SRC_DIR:-$INSTALL_ROOT/src/Evidune}"
VENV_DIR="${EVIDUNE_VENV_DIR:-$INSTALL_ROOT/venv}"
BIN_DIR="${EVIDUNE_BIN_DIR:-$HOME/.local/bin}"
REF="${EVIDUNE_REF:-main}"

log() {
  printf '==> %s\n' "$*"
}

fail() {
  printf 'error: %s\n' "$*" >&2
  exit 1
}

require_cmd() {
  command -v "$1" >/dev/null 2>&1 || fail "required command not found: $1"
}

clone_or_update_repo() {
  mkdir -p "$(dirname "$SRC_DIR")"

  if [ -d "$SRC_DIR/.git" ]; then
    log "Updating existing Evidune checkout in $SRC_DIR"
    git -C "$SRC_DIR" remote set-url origin "$REPO_SSH"
    git -C "$SRC_DIR" fetch --tags origin
    git -C "$SRC_DIR" checkout "$REF"
    git -C "$SRC_DIR" pull --ff-only origin "$REF"
    return
  fi

  rm -rf "$SRC_DIR"
  if command -v gh >/dev/null 2>&1 && gh auth status >/dev/null 2>&1; then
    log "Cloning $REPO_GH with GitHub CLI"
    gh repo clone "$REPO_GH" "$SRC_DIR"
  else
    log "Cloning $REPO_SSH with git+ssh"
    git clone "$REPO_SSH" "$SRC_DIR"
  fi

  git -C "$SRC_DIR" checkout "$REF"
}

install_runtime() {
  log "Creating virtual environment in $VENV_DIR"
  python3 -m venv "$VENV_DIR"
  "$VENV_DIR/bin/python" -m pip install --upgrade pip setuptools wheel
  "$VENV_DIR/bin/pip" install -e "$SRC_DIR[all]"
}

install_launcher() {
  mkdir -p "$BIN_DIR"
  cat >"$BIN_DIR/evidune" <<EOF
#!/bin/sh
exec "$VENV_DIR/bin/evidune" "\$@"
EOF
  chmod +x "$BIN_DIR/evidune"
}

print_path_hint() {
  case ":$PATH:" in
    *":$BIN_DIR:"*) ;;
    *)
      printf '\nAdd %s to PATH to invoke `evidune` directly.\n' "$BIN_DIR"
      ;;
  esac
}

main() {
  require_cmd git
  require_cmd python3

  mkdir -p "$INSTALL_ROOT"
  clone_or_update_repo
  install_runtime
  install_launcher

  log "Installed Evidune."
  printf 'Launcher: %s/evidune\n' "$BIN_DIR"
  print_path_hint
}

main "$@"
