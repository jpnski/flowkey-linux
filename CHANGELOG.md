# Changelog

## 2.0.0-dev — Linux Port

First Linux-compatible release of the FastFlowPrompt/Flowkey desktop assistant.

### Added

- Linux-native path resolution via `$XDG_DATA_HOME` / `~/.local/share`
- XDG autostart `.desktop` file support (replaces Windows Registry Run key)
- `notify-send` desktop notifications (replaces PowerShell toasts)
- Linux process management via `os.kill()`, `/proc/`, `ss -tlnp` (replaces `tasklist`/`taskkill`/`netstat`)
- `pyproject.toml` optional dependencies for X11 (`pynput`) and Wayland (`python-evdev`)
- New console scripts: `ffp-listener`, `ffp-tray`, `ffp-dashboard`

### Changed

- **Pure Linux codebase** — all Windows-specific code removed (AutoHotkey, PowerShell, WinAPI, Registry)
- `paths.py` — `_is_under_program_files()` → `_is_under_prefix()`, uses XDG base dir spec
- `subprocess_util.py` — no `CREATE_NO_WINDOW` flags
- `notify.py` — `notify-send` with stderr fallback (was `ffp_notify.py`)
- `flm_server.py` — `is_pid_alive()` uses `os.kill(pid, 0)`, `kill_pid()` uses `os.kill(pid, SIGTERM)`, `find_pids_on_port()` uses `ss -tlnp` (was `ffp_flm_server.py`)
- `daemon.py` — parent-PID watching via `/proc/` polling; autostart via XDG `.desktop` file (was `ffp_daemon.py`)
- `first_run.py` — NPU detection via `flm validate` instead of PowerShell
- `install.py` — Linux system setup (groups, autostart, model pull)
- Project renamed to `flowkey` in `pyproject.toml` (package name `fastflowprompt` kept for backward compat)
- Updated classifiers for Linux (POSIX, X11, Wayland)

### Removed

- All Windows-specific modules: AHK scripts, Inno Setup installer, PowerShell helpers
- WinAPI parent-watch (`ctypes.windll`, `kernel32`, `WaitForSingleObject`)
- Windows Registry autostart (`winreg`)
- AMD NPU PowerShell detection
- `.exe` references everywhere
- Update ZIP extraction validates paths before unpacking.
