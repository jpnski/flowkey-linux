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
- CLI commands: `flowkey-daemon`, `flowkey-listener`, `flowkey-tray`, `flowkey-tui`, `flowkey-install`, `flowkey-grammar-fix`
- `install.sh` for Debian/RPM system bootstrap; GitHub Actions CI on `ubuntu-latest`

### Removed
- All Windows-specific code: AutoHotkey scripts, Inno Setup installer, PowerShell helpers, WinAPI parent-watch, Registry autostart, AMD NPU PowerShell detection, `.exe` references

### Changed
*(none yet — post-port incremental changes will be logged here)*

### Fixed
*(none yet)*

> Detailed per-component porting steps are recorded in [`TODO.md`](TODO.md) (Phases 1–6).
