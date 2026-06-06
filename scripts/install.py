"""Flowkey Linux installer.

Run after `pip install .` has placed the package on disk. Handles the parts
pip can't:

- System dependency checks (xdotool, ydotool, notify-send, etc.)
- Config bootstrap (copy example → live)
- Input group + udev for evdev hotkey capture
- XDG autostart .desktop file creation
- Model pull (`flm pull gemma4-it:e4b`)

Invocation:
  ffp-install                   # full flow
  ffp-install --model <name>    # pull a different default model
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import paths as _paths

import config

HERE = Path(__file__).resolve().parent

_paths.ensure_dirs()
CONFIG_LIVE = _paths.CONFIG_FILE
CONFIG_EXAMPLE = _paths.CONFIG_EXAMPLE_FILE

# Default model to pull on first install.
DEFAULT_MODEL = "gemma4-it:e4b"

# Linux tools needed for full functionality.
OPTIONAL_TOOLS = {
    "xdotool": "X11 window info and key simulation",
    "ydotool": "Wayland key simulation (paste-back)",
    "notify-send": "Desktop notifications",
    "wl-paste": "Wayland clipboard access",
}

# Groups the user should be in for evdev hotkey capture.
REQUIRED_GROUPS = ["input"]


def _step(msg: str) -> None:
    print(f"[Flowkey] {msg}", flush=True)


# ---------- Prereq detection -------------------------------------------------

def _has_cmd(name: str) -> bool:
    return shutil.which(name) is not None


def _check_prereqs() -> dict:
    status = {"python": True}  # we're running, so Python is here
    for tool, desc in OPTIONAL_TOOLS.items():
        status[tool] = _has_cmd(tool)
        if status[tool]:
            _step(f"  {tool}: found ({desc})")
        else:
            _step(f"  {tool}: not found ({desc} — optional)")
    # flm (FastFlowLM) is the core dependency
    status["flm"] = _has_cmd("flm")
    return status


def _check_groups() -> list[str]:
    """Check which required groups the current user is in."""
    try:
        result = subprocess.run(["groups"], capture_output=True, text=True, check=False)
        user_groups = set(result.stdout.strip().split())
    except Exception:
        return REQUIRED_GROUPS  # can't check — assume missing
    return [g for g in REQUIRED_GROUPS if g not in user_groups]


# ---------- Config bootstrap -------------------------------------------------

def _ensure_config() -> None:
    if CONFIG_LIVE.exists():
        return
    if CONFIG_EXAMPLE.exists():
        shutil.copyfile(CONFIG_EXAMPLE, CONFIG_LIVE)
        _step("Created grammar_hotkey.config.json from example.")
        return
    CONFIG_LIVE.write_text(
        json.dumps(config.DEFAULT_CONFIG, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    _step("Created grammar_hotkey.config.json from built-in defaults.")


# ---------- XDG autostart ----------------------------------------------------

def _ensure_autostart() -> None:
    """Create ~/.config/autostart/ffp-listener.desktop if it doesn't exist."""
    autostart_dir = Path(os.path.expanduser("~/.config/autostart"))
    autostart_file = autostart_dir / "ffp-listener.desktop"
    if autostart_file.exists():
        _step("Autostart entry already exists.")
        return
    desktop_content = (
        "[Desktop Entry]\n"
        "Type=Application\n"
        "Name=Flowkey Listener\n"
        "Comment=Flowkey global hotkey listener\n"
        "Exec=ffp-listener\n"
        "Terminal=false\n"
        "X-GNOME-Autostart-enabled=true\n"
    )
    try:
        autostart_dir.mkdir(parents=True, exist_ok=True)
        autostart_file.write_text(desktop_content)
        _step(f"Created autostart entry: {autostart_file}")
    except OSError as exc:
        _step(f"Warning: could not create autostart entry: {exc}")


# ---------- Model management -------------------------------------------------

def _model_installed(name: str) -> bool:
    if not _has_cmd("flm"):
        return False
    try:
        result = subprocess.run(
            ["flm", "list", "--json"],
            capture_output=True, text=True, timeout=15, check=False,
        )
    except Exception:
        return False
    try:
        data = json.loads(result.stdout)
        for model in data.get("models") or []:
            if model.get("model") == name and model.get("installed"):
                return True
    except (ValueError, json.JSONDecodeError):
        pass
    return name in (result.stdout or "")


def _pull_model(name: str) -> bool:
    if not _has_cmd("flm"):
        _step("flm command not found — cannot pull model.")
        return False
    _step(f"Pulling model {name} (first run may take several minutes)...")
    try:
        result = subprocess.run(["flm", "pull", name], check=False)
    except Exception as e:
        _step(f"flm pull failed: {e}")
        return False
    if result.returncode != 0:
        _step(f"flm pull exited with code {result.returncode}.")
        return False
    return True


# ---------- Main -------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(description="Flowkey Linux installer.")
    parser.add_argument("--model", default=DEFAULT_MODEL,
                        help=f"Model to pull (default: {DEFAULT_MODEL}).")
    args = parser.parse_args()

    _step("Flowkey Linux Setup")
    _step("=" * 40)

    _step("Checking prerequisites...")
    prereqs = _check_prereqs()
    missing_tools = [k for k, v in prereqs.items() if not v and k != "python"]
    if missing_tools:
        _step(f"Optional tools not found: {', '.join(missing_tools)}")
        _step("Install them via your package manager for full functionality.")
        _step("  sudo apt install xdotool ydotool wl-clipboard libnotify-bin  (Debian/Ubuntu)")
        _step("  sudo dnf install xdotool ydotool wl-clipboard libnotify      (Fedora)")

    # Input group check
    missing_groups = _check_groups()
    if missing_groups:
        _step(f"User not in required groups: {', '.join(missing_groups)}")
        _step(f"Run: sudo usermod -aG {' '.join(missing_groups)} $USER")
        _step("Then log out and back in for evdev hotkey capture to work.")

    _ensure_config()

    # Autostart
    _ensure_autostart()

    # Model pull
    if _model_installed(args.model):
        _step(f"Model {args.model} already installed.")
    else:
        if not _pull_model(args.model):
            _step("Model pull failed or was skipped. You can pull manually: flm pull <model>")
            return 1

    _step("Setup complete.")
    _step("Run 'ffp-daemon' to start the action daemon.")
    _step("Run 'ffp-listener' to start the global hotkey listener (requires daemon).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
