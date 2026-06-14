#!/usr/bin/env bash
# Flowkey — Linux system installer
#
# Usage:
#   ./install.sh              # binary install (downloads release asset into ~/.local)
#   ./install.sh --from-source # contributor install from the local checkout
#   ./install.sh --help       # this message
#
# What this script does:
#   1. Detect Linux distro and install system packages (apt/dnf/pacman/zypper)
#   2. Add current user to 'input' group for evdev hotkey capture
#   3. Install udev rule for /dev/input/event* (Wayland hotkeys)
#   4. Download and install the binary release into ~/.local/opt/flowkey/current
#   5. Symlink ~/.local/bin/flowkey to the installed binary
#   6. Create ~/.local/share/applications/flowkey.desktop
#   7. Run 'flowkey install' for config bootstrap, autostart, and model pull
#
# This script is idempotent — safe to re-run.
#
# Dependencies installed by package manager:
#   - xdotool (X11 window info + key simulation)
#   - ydotool (Wayland key simulation)
#   - wl-clipboard (Wayland clipboard)
#   - libnotify-bin / libnotify (notify-send)
#   - python3-pyperclip, python3-pynput, python3-evdev,
#     python3-pystray, python3-textual (Python packages,
#     installed via pip, NOT via apt — listed here for reference)
#
# Contributor installs can still use pip from pyproject.toml.

set -euo pipefail

SCRIPT_SOURCE="${BASH_SOURCE[0]:-}"
SCRIPT_DIR=""
if [[ -n "$SCRIPT_SOURCE" && "$SCRIPT_SOURCE" != /dev/fd/* && -f "$SCRIPT_SOURCE" ]]; then
  SCRIPT_DIR="$(cd "$(dirname "$SCRIPT_SOURCE")" && pwd)"
fi
APP_NAME="Flowkey"

# ── Color helpers ────────────────────────────────────────────────────────────
if [[ -t 1 ]]; then
  GREEN='\033[0;32m'; YELLOW='\033[1;33m'; RED='\033[0;31m'; NC='\033[0m'
else
  GREEN=''; YELLOW=''; RED=''; NC=''
fi
log()  { printf "${GREEN}[${APP_NAME}]${NC} %s\n" "$*"; }
warn() { printf "${YELLOW}[${APP_NAME} WARN]${NC} %s\n" "$*" >&2; }
fail() { printf "${RED}[${APP_NAME} ERROR]${NC} %s\n" "$*" >&2; exit 1; }

# ── Help ─────────────────────────────────────────────────────────────────────
usage() {
  if [[ -n "$SCRIPT_SOURCE" && -r "$SCRIPT_SOURCE" ]]; then
    sed -n '2,/^$/{ s/^# \?//p }' "$SCRIPT_SOURCE"
  else
    cat <<'EOF'
Flowkey — Linux system installer

Usage:
  ./install.sh              # binary install (downloads release asset into ~/.local)
  ./install.sh --from-source # contributor install from the local checkout
  ./install.sh --help       # this message

What this script does:
  1. Detect Linux distro and install system packages (apt/dnf/pacman/zypper)
  2. Add current user to 'input' group for evdev hotkey capture
  3. Install udev rule for /dev/input/event* (Wayland hotkeys)
  4. Download and install the binary release into ~/.local/opt/flowkey/current
  5. Symlink ~/.local/bin/flowkey to the installed binary
  6. Create ~/.local/share/applications/flowkey.desktop
  7. Run 'flowkey install' for config bootstrap, autostart, and model pull
EOF
  fi
  exit 0
}

# ── Parse arguments ──────────────────────────────────────────────────────────
INSTALL_MODE="binary"  # binary | from-source

while [[ $# -gt 0 ]]; do
  case "$1" in
    --help) usage ;;
    --from-source) INSTALL_MODE="from-source"; shift ;;
    *) fail "Unknown option: $1. Use --help for usage." ;;
  esac
done

# ── Prerequisite checks ──────────────────────────────────────────────────────
[[ "$(uname -s)" == "Linux" ]] || fail "This installer is for Linux only."

command -v python3 >/dev/null 2>&1 || fail "python3 is required (Python 3.11+)."
PYVER="$(python3 --version 2>&1 | awk '{print $2}')"
log "Python $PYVER detected."

# Check Python version >= 3.11
python3 -c '
import sys
if sys.version_info < (3, 11):
    sys.exit(1)
' 2>/dev/null || fail "Python 3.11+ required (got $PYVER)."

# pip check
command -v pip3 >/dev/null 2>&1 || python3 -m pip --version >/dev/null 2>&1 || {
  warn "pip not found. Installing python3-pip may be needed."
}

# ── Distro detection ─────────────────────────────────────────────────────────
detect_distro() {
  if command -v apt-get >/dev/null 2>&1; then
    echo "debian"
  elif command -v dnf >/dev/null 2>&1; then
    echo "fedora"
  elif command -v pacman >/dev/null 2>&1; then
    echo "arch"
  elif command -v zypper >/dev/null 2>&1; then
    echo "suse"
  elif [[ -f /etc/os-release ]]; then
    # Fallback: try ID field
    local id
    id="$(grep -oP '^ID=\K.*' /etc/os-release 2>/dev/null || echo "unknown")"
    case "$id" in
      ubuntu|debian|linuxmint|pop) echo "debian" ;;
      fedora|rhel|centos)          echo "fedora" ;;
      arch|manjaro|endeavouros)    echo "arch" ;;
      opensuse*)                   echo "suse" ;;
      *)                           echo "unknown" ;;
    esac
  else
    echo "unknown"
  fi
}

run_privileged() {
  if [[ "$EUID" -eq 0 ]]; then
    "$@"
  elif command -v sudo >/dev/null 2>&1; then
    sudo "$@"
  else
    return 1
  fi
}

DISTRO="$(detect_distro)"
log "Detected distribution family: ${DISTRO}"

# ── System package installation ──────────────────────────────────────────────
install_system_packages() {
  local missing=()

  # Check which packages are needed
  # (These are the system-level dependencies for the OS-specific tools)
  # Python packages (pyperclip, pynput, evdev, pystray, textual) are
  # installed via pip, NOT via the system package manager.

  case "$DISTRO" in
    debian)
      local pkg_list=(
        xdotool
        ydotool
        wl-clipboard
        libnotify-bin
        python3-dev
        libx11-dev
      )
      # On Debian/Ubuntu, 'xdg-utils' is usually already present but ensure:
      log "Debian/Ubuntu detected. Installing system packages..."
       run_privileged apt-get update -qq || warn "apt update failed (continuing)"
       run_privileged apt-get install -y -qq "${pkg_list[@]}" || {
        warn "Some packages failed to install. Check individual package names for your release."
        return 1
      }
      ;;
    fedora)
      local pkg_list=(
        xdotool
        ydotool
        wl-clipboard
        libnotify
        python3-devel
        libX11-devel
      )
      log "Fedora/RHEL detected. Installing system packages..."
       run_privileged dnf install -y "${pkg_list[@]}" || {
        warn "Some packages failed to install. Check individual package names for your release."
        return 1
      }
      ;;
    arch)
      local pkg_list=(
        xdotool
        ydotool
        wl-clipboard
        libnotify
        python
      )
      log "Arch Linux detected. Installing system packages..."
       run_privileged pacman -S --needed --noconfirm "${pkg_list[@]}" || {
        warn "Some packages failed to install. Check individual package names for your system."
        return 1
      }
      ;;
    suse)
      local pkg_list=(
        xdotool
        ydotool
        wl-clipboard
        libnotify
        python3-devel
        libX11-devel
      )
      log "openSUSE detected. Installing system packages..."
       run_privileged zypper install -y "${pkg_list[@]}" || {
        warn "Some packages failed to install. Check individual package names for your release."
        return 1
      }
      ;;
    *)
      warn "Unrecognized distro. Skipping system package installation."
      warn "Install manually: xdotool, ydotool, wl-clipboard, libnotify-bin"
      return 0
      ;;
  esac

  # Also try to install evdev development headers (needed for python-evdev build)
  # but don't fail if unavailable.
  case "$DISTRO" in
    debian)
       run_privileged apt-get install -y -qq libevdev-dev 2>/dev/null || true ;;
    fedora)
       run_privileged dnf install -y libevdev-devel 2>/dev/null || true ;;
    arch)
      # libevdev is usually already in base-devel
      true ;;
    suse)
       run_privileged zypper install -y libevdev-devel 2>/dev/null || true ;;
  esac

  log "System package installation complete."
  return 0
}

# Distro detection comparison with the TODO requirement:
# TODO says "Detect Linux distribution (apt vs rpm)". We support apt, dnf, pacman, zypper.

# ── User groups ──────────────────────────────────────────────────────────────
setup_groups() {
  if groups "$USER" 2>/dev/null | grep -qw "input"; then
    log "User already in 'input' group."
  else
    log "Adding user '$USER' to 'input' group for evdev access..."
    run_privileged usermod -aG input "$USER" || warn "Could not add to 'input' group."
    log "You may need to log out and back in for group changes to take effect."
  fi
}

# ── udev rule ────────────────────────────────────────────────────────────────
setup_udev() {
  local rule_file="/etc/udev/rules.d/99-flowkey-listener.rules"
  local rule='KERNEL=="event*", GROUP="input", MODE="0660"'

  if [[ -f "$rule_file" ]] && grep -q "event\*" "$rule_file" 2>/dev/null; then
    log "udev rule already present: $rule_file"
    return 0
  fi

  log "Installing udev rule for evdev hotkey capture..."
  echo "$rule" | run_privileged tee "$rule_file" >/dev/null || {
    warn "Could not write $rule_file. evdev hotkeys (Wayland) may need manual setup."
    return 1
  }
  run_privileged udevadm control --reload-rules 2>/dev/null || true
  run_privileged udevadm trigger 2>/dev/null || true
  log "udev rule installed. Re-plug any input devices or reboot to apply."
}

# ── Binary payload install ───────────────────────────────────────────────────
FLOWKEY_RELEASE_REPO="${FLOWKEY_RELEASE_REPO:-jpnski/flowkey-linux}"
FLOWKEY_RELEASE_TAG="${FLOWKEY_RELEASE_TAG:-latest}"
FLOWKEY_RELEASE_TARBALL="${FLOWKEY_RELEASE_TARBALL:-}"
FLOWKEY_INSTALL_ROOT="${FLOWKEY_INSTALL_ROOT:-${HOME}/.local/opt/flowkey/current}"
FLOWKEY_BIN_DIR="${FLOWKEY_BIN_DIR:-${HOME}/.local/bin}"
FLOWKEY_BIN_PATH="${FLOWKEY_BIN_PATH:-${FLOWKEY_BIN_DIR}/flowkey}"
FLOWKEY_SKIP_SYSTEM_SETUP="${FLOWKEY_SKIP_SYSTEM_SETUP:-0}"

detect_arch() {
  case "$(uname -m)" in
    x86_64|amd64) echo "x86_64" ;;
    aarch64|arm64) echo "aarch64" ;;
    *) fail "Unsupported architecture: $(uname -m) (expected x86_64 or aarch64)." ;;
  esac
}

resolve_release_tag() {
  if [[ -n "$FLOWKEY_RELEASE_TAG" && "$FLOWKEY_RELEASE_TAG" != "latest" ]]; then
    echo "$FLOWKEY_RELEASE_TAG"
    return 0
  fi
  curl -fsSL "https://api.github.com/repos/${FLOWKEY_RELEASE_REPO}/releases/latest" \
    | python3 -c 'import json, sys; print(json.load(sys.stdin)["tag_name"])'
}

resolve_icon_path() {
  local candidates=(
    "${FLOWKEY_INSTALL_ROOT}/_internal/assets/flowkey.png"
    "${FLOWKEY_INSTALL_ROOT}/assets/flowkey.png"
    "${SCRIPT_DIR}/scripts/assets/flowkey.png"
    "${SCRIPT_DIR}/assets/flowkey.png"
  )
  local candidate
  for candidate in "${candidates[@]}"; do
    if [[ -f "$candidate" ]]; then
      echo "$candidate"
      return 0
    fi
  done
  echo "flowkey"
}

create_tui_desktop_entry() {
  local apps_dir="${HOME}/.local/share/applications"
  local desktop_file="${apps_dir}/flowkey.desktop"
  local icon_path

  mkdir -p "$apps_dir"

  if [[ -f "$desktop_file" ]]; then
    log "TUI desktop entry already exists: ${desktop_file}"
    return 0
  fi

  icon_path="$(resolve_icon_path)"

  cat > "$desktop_file" <<DESKTOP_EOF
[Desktop Entry]
Type=Application
Name=Flowkey Chat
Comment=Flowkey local LLM chat interface
Exec=flowkey tui
Icon=${icon_path}
Terminal=true
Categories=Utility;TextTools;
Keywords=llm;chat;grammar;ai;
DESKTOP_EOF

  chmod 644 "$desktop_file"
  log "Created TUI desktop entry: ${desktop_file}"

  if command -v update-desktop-database >/dev/null 2>&1; then
    update-desktop-database "$apps_dir" 2>/dev/null || true
  fi
}

install_binary_release() {
  local arch tag asset_name release_url tmpdir extract_dir payload_dir payload_file payload_target

  arch="$(detect_arch)"
  asset_name="flowkey-linux-${arch}.tar.gz"

  command -v tar >/dev/null 2>&1 || fail "tar is required to unpack the release binary."

  tmpdir="$(mktemp -d)"
  extract_dir="${tmpdir}/extract"
  mkdir -p "$extract_dir"

  if [[ -n "$FLOWKEY_RELEASE_TARBALL" ]]; then
    log "Using local Flowkey release archive (${FLOWKEY_RELEASE_TARBALL})."
    cp "$FLOWKEY_RELEASE_TARBALL" "${tmpdir}/${asset_name}" || fail "Failed to copy ${FLOWKEY_RELEASE_TARBALL}."
  else
    local release_url
    local tag

    tag="$(resolve_release_tag)"
    release_url="https://github.com/${FLOWKEY_RELEASE_REPO}/releases/download/${tag}/${asset_name}"
    command -v curl >/dev/null 2>&1 || fail "curl is required to download the release binary."
    log "Downloading Flowkey ${tag} (${arch})..."
    curl -fsSL "$release_url" -o "${tmpdir}/${asset_name}" || fail "Failed to download ${release_url}."
  fi

  tar -xzf "${tmpdir}/${asset_name}" -C "$extract_dir" || fail "Failed to unpack ${asset_name}."

  payload_dir=""
  payload_file=""
  for candidate in "$extract_dir"/*; do
    if [[ -d "$candidate" && -f "$candidate/flowkey" ]]; then
      payload_dir="$candidate"
      break
    elif [[ -f "$candidate" && "$(basename "$candidate")" == "flowkey" ]]; then
      payload_file="$candidate"
      break
    fi
  done

  mkdir -p "$(dirname "$FLOWKEY_INSTALL_ROOT")" "$FLOWKEY_BIN_DIR"
  payload_target="${FLOWKEY_INSTALL_ROOT}.new"
  rm -rf "$payload_target"
  if [[ -n "$payload_dir" ]]; then
    mv "$payload_dir" "$payload_target" || fail "Failed to stage Flowkey payload into ${payload_target}."
  elif [[ -n "$payload_file" ]]; then
    mkdir -p "$payload_target"
    mv "$payload_file" "$payload_target/flowkey" || fail "Failed to stage Flowkey payload into ${payload_target}."
    chmod +x "$payload_target/flowkey" 2>/dev/null || true
  else
    fail "Release archive does not contain a flowkey payload directory or binary."
  fi
  rm -rf "$FLOWKEY_INSTALL_ROOT"
  mv "$payload_target" "$FLOWKEY_INSTALL_ROOT" || fail "Failed to install Flowkey into ${FLOWKEY_INSTALL_ROOT}."

  chmod +x "${FLOWKEY_INSTALL_ROOT}/flowkey" 2>/dev/null || true
  ln -sfn "${FLOWKEY_INSTALL_ROOT}/flowkey" "$FLOWKEY_BIN_PATH" || fail "Failed to create ${FLOWKEY_BIN_PATH}."
  log "Installed Flowkey binary to ${FLOWKEY_INSTALL_ROOT}."
  log "Linked command at ${FLOWKEY_BIN_PATH}."
}

run_source_install() {
  local pip_cmd pip_args=()

  log "Installing Flowkey from source checkout..."
  [[ -n "$SCRIPT_DIR" ]] || fail "--from-source requires running install.sh from a checked-out repo, not curl | bash."

  if [[ -n "${VIRTUAL_ENV:-}" && -x "${VIRTUAL_ENV}/bin/python" ]]; then
    pip_cmd="${VIRTUAL_ENV}/bin/python -m pip"
  else
    pip_cmd="python3 -m pip"
    if [[ "$EUID" -ne 0 ]]; then
      pip_args+=(--user)
    fi
  fi

  $pip_cmd install --upgrade --no-cache-dir "${pip_args[@]}" "$SCRIPT_DIR" || \
    fail "Source install failed. Check the output above."
  log "Source install complete."
}

resolve_flowkey_command() {
  if [[ -x "$FLOWKEY_BIN_PATH" ]]; then
    echo "$FLOWKEY_BIN_PATH"
    return 0
  fi
  if command -v flowkey >/dev/null 2>&1; then
    command -v flowkey
    return 0
  fi
  if [[ -n "${VIRTUAL_ENV:-}" && -x "${VIRTUAL_ENV}/bin/flowkey" ]]; then
    echo "${VIRTUAL_ENV}/bin/flowkey"
    return 0
  fi
  echo "${HOME}/.local/bin/flowkey"
}

# ── Post-install summary ─────────────────────────────────────────────────────
print_summary() {
  local current_user

  current_user="${USER:-$(id -un 2>/dev/null || true)}"

  echo
  log "══════════════════════════════════════════════════"
  log "  ${APP_NAME} installation complete!"
  log "══════════════════════════════════════════════════"
  echo
  log "Next steps:"
  echo
  log "  1. Log out and back in (or reboot) to apply group/udev changes."
  echo
  log "  2. Start the action daemon:"
  log "       flowkey daemon"
  echo
  log "  3. Start the global hotkey listener (requires daemon):"
  log "       flowkey listen"
  echo
  log "  4. Or launch the TUI directly:"
  log "       flowkey tui"
  echo
  log "  5. (Optional) Run the installer config step again:"
  log "       flowkey install"
  echo
  log "  For Wayland: install ydotool and ensure you're in the 'input' group."
  log "  For X11: xdotool and pynput handle everything."
  echo

  if [[ -n "$current_user" ]] && groups "$current_user" 2>/dev/null | grep -qw "input"; then
    :
  else
    warn "You were added to the 'input' group. Log out and back in for evdev hotkeys to work."
  fi
}

# ── Post-install summary ─────────────────────────────────────────────────────
print_summary() {
  local current_user

  current_user="${USER:-$(id -un 2>/dev/null || true)}"

  echo
  log "══════════════════════════════════════════════════"
  log "  ${APP_NAME} installation complete!"
  log "══════════════════════════════════════════════════"
  echo
  log "Next steps:"
  echo
  log "  1. Log out and back in (or reboot) to apply group/udev changes."
  echo
  log "  2. Start the action daemon:"
  log "       flowkey daemon"
  echo
  log "  3. Start the global hotkey listener (requires daemon):"
  log "       flowkey listen"
  echo
  log "  4. Or launch the TUI directly:"
  log "       flowkey tui"
  echo
  log "  5. (Optional) Add to autostart:"
  log "       flowkey install"
  echo
  log "  For Wayland: install ydotool and ensure you're in the 'input' group."
  log "  For X11: xdotool and pynput handle everything."
  echo

  # Remind about group if just added
  if [[ -n "$current_user" ]] && groups "$current_user" 2>/dev/null | grep -qw "input"; then
    :  # already in group
  else
    warn "You were added to the 'input' group. Log out and back in for evdev hotkeys to work."
  fi
}

# ── Main ─────────────────────────────────────────────────────────────────────
main() {
  local flowkey_cmd

  log "${APP_NAME} Linux Installer"
  log "Mode: ${INSTALL_MODE}"
  echo

  if [[ "$FLOWKEY_SKIP_SYSTEM_SETUP" == "1" ]]; then
    log "Skipping system packages, groups, and udev setup (test override)."
  elif run_privileged true >/dev/null 2>&1; then
    install_system_packages || warn "Some system packages may be missing."
    setup_groups || warn "Could not update user groups."
    setup_udev || warn "Could not install udev rules."
  else
    warn "No sudo available; skipping package manager, group, and udev setup."
  fi

  if [[ "$INSTALL_MODE" == "from-source" ]]; then
    run_source_install
  else
    install_binary_release
  fi

  create_tui_desktop_entry
  flowkey_cmd="$(resolve_flowkey_command)"
  log "Running Flowkey bootstrap via ${flowkey_cmd} install..."
  "$flowkey_cmd" install || warn "flowkey install reported issues (check above)."
  print_summary
}

main "$@"
