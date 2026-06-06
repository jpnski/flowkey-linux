#!/usr/bin/env bash
# Flowkey — Linux system installer
#
# Usage:
#   ./install.sh              # full system-wide install (requires sudo for system deps)
#   ./install.sh --user       # user-local install (pip install --user, no sudo needed)
#   ./install.sh --venv PATH  # install into a venv at PATH (creates if missing)
#   ./install.sh --help       # this message
#
# What this script does:
#   1. Detect Linux distro and install system packages (apt/dnf/pacman/zypper)
#   2. Add current user to 'input' group for evdev hotkey capture
#   3. Install udev rule for /dev/input/event* (Wayland hotkeys)
#   4. Run pip install (system-wide, --user, or into a venv)
#   5. Run 'flowkey-install' for config bootstrap, autostart, and model pull
#   6. Create ~/.local/share/applications/flowkey-tui.desktop
#   7. Convert assets/flowkey.ico → flowkey.png if ImageMagick present (fallback)
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
# Python packages are installed via pip from pyproject.toml.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
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
  sed -n '2,/^$/{ s/^# \?//p }' "$0"
  exit 0
}

# ── Parse arguments ──────────────────────────────────────────────────────────
INSTALL_MODE="system"  # system | user | venv
VENV_PATH=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --help) usage ;;
    --user) INSTALL_MODE="user"; shift ;;
    --venv) INSTALL_MODE="venv"; VENV_PATH="$2"; shift 2 ;;
    --venv=*) INSTALL_MODE="venv"; VENV_PATH="${1#*=}"; shift ;;
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
      sudo apt-get update -qq || warn "apt update failed (continuing)"
      sudo apt-get install -y -qq "${pkg_list[@]}" || {
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
      sudo dnf install -y "${pkg_list[@]}" || {
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
      sudo pacman -S --needed --noconfirm "${pkg_list[@]}" || {
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
      sudo zypper install -y "${pkg_list[@]}" || {
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
      sudo apt-get install -y -qq libevdev-dev 2>/dev/null || true ;;
    fedora)
      sudo dnf install -y libevdev-devel 2>/dev/null || true ;;
    arch)
      # libevdev is usually already in base-devel
      true ;;
    suse)
      sudo zypper install -y libevdev-devel 2>/dev/null || true ;;
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
    sudo usermod -aG input "$USER" || warn "Could not add to 'input' group."
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
  echo "$rule" | sudo tee "$rule_file" >/dev/null || {
    warn "Could not write $rule_file. evdev hotkeys (Wayland) may need manual setup."
    return 1
  }
  sudo udevadm control --reload-rules 2>/dev/null || true
  sudo udevadm trigger 2>/dev/null || true
  log "udev rule installed. Re-plug any input devices or reboot to apply."
}

# ── Icon conversion (fallback) ───────────────────────────────────────────────
ensure_icon() {
  local ico_path="${SCRIPT_DIR}/scripts/assets/flowkey.ico"
  local png_path="${SCRIPT_DIR}/scripts/assets/flowkey.png"

  if [[ -f "$png_path" ]]; then
    log "Icon: flowkey.png exists (${png_path})"
    return 0
  fi

  if [[ -f "$ico_path" ]]; then
    if command -v convert >/dev/null 2>&1; then
      log "Converting flowkey.ico → flowkey.png..."
      convert "$ico_path" "$png_path" || warn "Icon conversion failed (non-fatal)."
    else
      warn "flowkey.ico found but ImageMagick not installed. Install ImageMagick or provide flowkey.png manually."
    fi
  else
    warn "No icon files found at scripts/assets/. Tray icon will be missing."
  fi
}

# ── pip install ──────────────────────────────────────────────────────────────
run_pip_install() {
  local pip_cmd

  case "$INSTALL_MODE" in
    system)
      log "Installing Flowkey system-wide..."
      pip_cmd="sudo python3 -m pip install --upgrade --no-cache-dir"
      ;;
    user)
      log "Installing Flowkey for current user (--user)..."
      pip_cmd="python3 -m pip install --user --upgrade --no-cache-dir"
      ;;
    venv)
      if [[ -z "$VENV_PATH" ]]; then
        fail "--venv requires a path argument."
      fi
      if [[ ! -f "${VENV_PATH}/bin/activate" ]]; then
        log "Creating virtual environment at ${VENV_PATH}..."
        python3 -m venv "$VENV_PATH" || fail "Failed to create venv at ${VENV_PATH}."
      fi
      log "Installing Flowkey into venv at ${VENV_PATH}..."
      pip_cmd="${VENV_PATH}/bin/pip install --upgrade --no-cache-dir"
      ;;
  esac

  # Build wheel first for cleaner install
  log "Building wheel..."
  (cd "$SCRIPT_DIR" && python3 -m build --wheel --no-isolation 2>/dev/null) || {
    log "Wheel build skipped (build module may be missing), using pip install directly."
  }

  # Install the package (from source, so pip picks up pyproject.toml)
  $pip_cmd "$SCRIPT_DIR" || fail "pip install failed. Check output above."
  log "Python package installed."
}

# ── XDG desktop entries ──────────────────────────────────────────────────────
create_tui_desktop_entry() {
  local apps_dir="${HOME}/.local/share/applications"
  local desktop_file="${apps_dir}/flowkey-tui.desktop"

  mkdir -p "$apps_dir"

  if [[ -f "$desktop_file" ]]; then
    log "TUI desktop entry already exists: ${desktop_file}"
    return 0
  fi

  local icon_path
  icon_path="$(command -v flowkey-tui >/dev/null 2>&1 && dirname "$(command -v flowkey-tui)" 2>/dev/null || echo "/usr/local/bin")"
  # Use the PNG icon path relative to the scripts dir
  icon_path="${SCRIPT_DIR}/scripts/assets/flowkey.png"
  [[ -f "$icon_path" ]] || icon_path="flowkey-tui"  # fall back to icon name

  cat > "$desktop_file" <<DESKTOP_EOF
[Desktop Entry]
Type=Application
Name=Flowkey Chat
Comment=Flowkey local LLM chat interface
Exec=flowkey-tui
Icon=${icon_path}
Terminal=true
Categories=Utility;TextTools;
Keywords=llm;chat;grammar;ai;
DESKTOP_EOF

  chmod 644 "$desktop_file"
  log "Created TUI desktop entry: ${desktop_file}"

  # Update desktop database if available
  if command -v update-desktop-database >/dev/null 2>&1; then
    update-desktop-database "$apps_dir" 2>/dev/null || true
  fi
}

# ── flowkey-install (Python setup) ───────────────────────────────────────────
run_flowkey_install() {
  local flowkey_install_cmd

  case "$INSTALL_MODE" in
    system)  flowkey_install_cmd="flowkey-install" ;;
    user)    flowkey_install_cmd="flowkey-install" ;;
    venv)    flowkey_install_cmd="${VENV_PATH}/bin/flowkey-install" ;;
  esac

  if command -v "$flowkey_install_cmd" >/dev/null 2>&1; then
    log "Running flowkey-install for config bootstrap and model pull..."
    $flowkey_install_cmd || warn "flowkey-install reported issues (check above)."
  else
    # Try as a Python module
    log "flowkey-install not on PATH, trying python -m install..."
    case "$INSTALL_MODE" in
      system) sudo python3 -m install || true ;;
      user)   python3 -m install || true ;;
      venv)   "${VENV_PATH}/bin/python" -m install || true ;;
    esac
  fi
}

# ── Post-install summary ─────────────────────────────────────────────────────
print_summary() {
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
  log "       flowkey-daemon"
  echo
  log "  3. Start the global hotkey listener (requires daemon):"
  log "       flowkey-listener"
  echo
  log "  4. Or launch the TUI directly:"
  log "       flowkey-tui"
  echo
  log "  5. (Optional) Add to autostart:"
  log "       flowkey-install"
  echo
  log "  For Wayland: install ydotool and ensure you're in the 'input' group."
  log "  For X11: xdotool and pynput handle everything."
  echo

  # Remind about group if just added
  if groups "$USER" 2>/dev/null | grep -qw "input"; then
    :  # already in group
  else
    warn "You were added to the 'input' group. Log out and back in for evdev hotkeys to work."
  fi
}

# ── Main ─────────────────────────────────────────────────────────────────────
main() {
  log "${APP_NAME} Linux Installer"
  if [[ "$INSTALL_MODE" == "venv" ]]; then
    log "Mode: venv (${VENV_PATH})"
  else
    log "Mode: ${INSTALL_MODE}"
  fi
  echo

  # System-level setup (requires sudo) — skip for --user and --venv
  if [[ "$INSTALL_MODE" == "system" ]]; then
    install_system_packages
    setup_groups
    setup_udev
  else
    log "Skipping system packages, groups, and udev (${INSTALL_MODE} mode)."
    log "Install system deps manually if needed: xdotool, ydotool, wl-clipboard, libnotify-bin"
  fi

  ensure_icon
  run_pip_install
  create_tui_desktop_entry
  run_flowkey_install
  print_summary
}

main "$@"
