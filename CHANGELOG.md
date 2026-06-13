# Changelog

All notable changes to the **Flowkey Linux port** are documented here.
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/);
this project uses [Semantic Versioning](https://semver.org/).

For the step-by-step port log, see [`TODO.md`](TODO.md).

## [Unreleased]

Initial Linux port of Flowkey, forked from upstream [Fastflow 1.5.4](https://github.com/agr77one/Fastflow).
Still under verification — see [`verification.md`](verification.md) for status.

### Added
- Linux-native backend: XDG paths, `notify-send`, `os.kill`/`/proc`/`ss` process management, XDG autostart `.desktop` files
- Global hotkey `listener` module with `pynput` (X11) and `evdev` (Wayland) backends
- Textual `tui` module — tabbed chat (streaming) + multi-panel dashboard, replaces the tkinter chat popup and dashboard
- System `tray` indicator via `pystray` with Wayland fallback
- Single CLI dispatcher: `flowkey daemon`, `flowkey listen`, `flowkey tray`, `flowkey tui`, `flowkey install`, `flowkey process`
- `install.sh` for Debian/RPM system bootstrap; GitHub Actions CI on `ubuntu-latest`
- Dashboard Config tab: interactive **FLM Model** panel — `Select` for the active model, `Collapsible` titled "Download a model" listing not-yet-installed models, live pull `ProgressBar` + status line + **Cancel pull** button, indeterminate `ProgressBar` + braille spinner shown while the FLM server restarts. Chat-stream guard (`ChatWidget.is_streaming()`) reverts the `Select` with a warning while a stream is in flight.
- Daemon action `pull_cancel` to terminate an in-flight `flm pull` (idempotent; returns `{"ok": False, "error": "no pull in progress"}` when idle). TUI exposes a "Cancel pull" button that appears only while a pull is running.

### Removed
- All Windows-specific code: AutoHotkey scripts, Inno Setup installer, PowerShell helpers, WinAPI parent-watch, Registry autostart, AMD NPU PowerShell detection, `.exe` references

> Detailed per-component porting steps are recorded in [`TODO.md`](TODO.md) (Phases 1–6).
