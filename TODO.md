# Flowkey Linux Port — Work Log

This document tracks the complete Linux port of [Fastflow/Flowkey](https://github.com/agr77one/Fastflow) to X11 and Wayland systems.

**Original repo (reference):** `~/devel/Fastflow` (cloned from `agr77one/Fastflow`)

---

## How to use this document

Each item is a single self-contained change. Items are in chronological order. Items with `[x]` are done. When you complete an item, mark it `[x]` and add the date + commit hash.

---

## Phase 1 — Python Backend Port (COMPLETE)

- [x] **1.** Update `paths.py` — detect Linux (`$XDG_DATA_HOME` / `~/.local/share`) instead of `%LOCALAPPDATA%`; replace `_is_under_program_files()` with `_is_under_prefix()` checking `/usr`, `/usr/local`, `/opt`
- [x] **2.** Update `subprocess_util.py` — pure Linux, no `CREATE_NO_WINDOW` flags
- [x] **3.** Rewrite `flm_server.py` (was `ffp_flm_server.py`) — replace `tasklist` / `taskkill` / `netstat -ano` with Linux equivalents: `os.kill(pid, 0)`, `os.kill(pid, SIGTERM)`, `ss -tlnp`
- [x] **4.** Rewrite `notify.py` (was `ffp_notify.py`) — replace PowerShell toast with `notify-send`, fall back to print-to-stderr
- [x] **5.** Replace `daemon.py` (was `ffp_daemon.py`) parent-PID watching — remove `ctypes.windll` WinAPI path, use `/proc/<parent_pid>/status` polling; remove Windows Registry autostart (replaced with XDG autostart)
- [x] **6.** Update `config.py` and `first_run.py` — replace `%USERPROFILE%` with `$HOME` for default vault path; remove NPU-detection (replaced with `flm validate`)
- [x] **7.** Run `pytest tests/ -q` — **81 passed, 1 skipped** (tkinter not available in headless CI — expected)

---

## Phase 2 — Dependencies, Naming & Project Config

- [x] **8.** Add new platform dependencies to `pyproject.toml` under `[project.optional-dependencies]`:
  - `pyperclip` — cross-platform clipboard access
  - `pynput` — X11 global hotkeys + key simulation
  - `python-evdev` — Wayland global hotkey capture (evdev)
  - `pystray` — system tray icon (X11)
- [x] **9.** Update `pyproject.toml`:
  - New modules added: `listener`, `tray`, `dashboard`
  - Console scripts registered
  - Classifiers updated for POSIX Linux
- [x] **10. Repo-wide naming standardization (`ffp_*` → `*`, `ffp-` → `flowkey-`)**:
  - `git mv` all `ffp_*.py` source files to drop `ffp_` prefix (9 files)
  - `git mv` all `test_ffp_*.py` test files to drop `ffp_` prefix (9 files)
  - Bulk rename all `ffp_` module references in imports and dotted-name usage across `scripts/` and `tests/` (e.g. `ffp_config` → `config`, `ffp_flm_server` → `flm_server`)
  - Bulk rename all `"ffp.*"` logger names to `"flowkey.*"` (15 modules)
  - Update `pyproject.toml` `py-modules` list and `[project.scripts]` entries (`ffp-daemon` → `flowkey-daemon`, etc.)
  - Update `CHANGELOG.md` and `TODO.md` old-name references
  - `pip install -e .` — reinstall with clean build artifacts
  - `pytest tests/ -q` — **81 passed, 2 skipped** (tkinter not available in headless CI)

---

## Phase 3 — Global Hotkey Listener (COMPLETE)

- [x] **11.** Create `scripts/listener.py` — module-level config loader:
  - `read_config()`: JSON load from `paths.CONFIG_FILE`, extract `hotkeys` object with keybindings, merge with defaults from `config.DEFAULT_CONFIG`
  - Store mode system prompts, FLM base URL, timeout values in module globals

- [x] **12.** In `listener.py` — implement daemon lifecycle:
  - `ensure_daemon_running()`: hit `http://127.0.0.1:52650/healthz` with 0.5s timeout via `urllib.request`; if unreachable, spawn daemon via `subprocess.Popen(["flowkey-daemon"])` and retry up to 10 times (5s total)

- [x] **13.** In `listener.py` — implement daemon dispatch:
  - `dispatch_action(action, body_json)`: HTTP POST to `http://127.0.0.1:52650/action/<action>` with `X-FFP-API: 1` header and JSON body
  - On any HTTP failure, fall back to subprocess: `subprocess.run(["flowkey-grammar-fix", "--app-action", action])`
  - Return parsed result string (parse daemon JSON response)

- [x] **14.** In `listener.py` — implement mode prefix parsing (port of `lib/mode_prefix.ahk`):
  - `parse_mode_and_text(text)`: regex match first non-empty line for `prompt:`, `summarize:`, `explain:`, `tone:` prefixes
  - Support `:` separator, ` - ` separator, inline and multiline body extraction
  - Return `(mode, body_text)`; default mode is `grammar`

- [x] **15.** In `listener.py` — implement clipboard helpers (port of `lib/clipboard.ahk`):
  - `clipboard_save()`: save current clipboard text via `pyperclip.paste()`
  - `clipboard_restore(text)`: `pyperclip.copy(text)`
  - `clipboard_capture()`: save clipboard, clear it (`pyperclip.copy("")`), simulate Ctrl+C (X11: `pynput.keyboard.Controller()`, Wayland: `xdotool key ctrl+c`), wait 200ms, read clipboard, restore original, return captured text

- [x] **16.** In `listener.py` — implement `paste_back(text)` (the hardest subproblem):
  - Copy `text` to clipboard via `pyperclip.copy(text)`
  - Detect `$XDG_SESSION_TYPE`:
    - X11: simulate Ctrl+V via `pynput.keyboard.Controller().press(Key.ctrl)`, `.press(KeyCode.from_char('v'))`, `.release()`, `.release()`
    - Wayland: attempt `subprocess.run(["ydotool", "key", "ctrl+v"])` if `ydotool` in PATH; if unavailable, call `notify-send "Flowkey" "Result copied — press Ctrl+V to paste"` and skip paste-back

- [x] **17.** In `listener.py` — implement keyboard capture init:
  - Detect `$XDG_SESSION_TYPE` at module load
  - If `x11`: start `pynput.keyboard.GlobalHotKeys` listener with combos from config
  - If `wayland`: open `/dev/input/event*` via `evdev.InputDevice`, match keycodes against configured combos
  - If neither: log error and exit

- [x] **18.** In `listener.py` — implement `process_selection()` (the grammar/prompt/summarize/explain/tone hotkey handler):
  - Call `clipboard_capture()` to get selected text
  - Call `parse_mode_and_text()` to extract mode prefix from body
  - Write body text to temp file via `tempfile.NamedTemporaryFile`
  - Run `subprocess.run(["flowkey-grammar-fix", "--mode", mode, "--input-file", infile, "--output-file", outfile])` with timeout from config
  - Read result from output temp file
  - Call `paste_back(result)`
  - Notify user of completion

- [x] **19.** In `listener.py` — implement `capture_note()` (Ctrl+Alt+N handler):
  - Call `clipboard_capture()` to get selection
  - If fresh capture is empty, fall back to existing clipboard content
  - Get active window name: X11 via `subprocess.run(["xdotool", "getactivewindow", "getwindowname"])`; Wayland: best-effort via `swaymsg -t get_tree` or empty string
  - POST to daemon action `save_note` with `{"text": captured, "source_app": window_name}`
  - Call `notify("Note saved")`

- [x] **20.** In `listener.py` — implement `launch_tui()` (Ctrl+Shift+T handler):
  - POST `chat_restart` action to daemon
  - Wait 200ms
  - Spawn `subprocess.Popen(["flowkey-tui"])`

- [x] **21.** In `listener.py` — implement `ask_with_selection()` (Ctrl+Shift+A handler):
  - Call `clipboard_capture()` to get selection
  - Get active window name (same as step 18)
  - POST to daemon action `chat_send_selection` with `{"text": captured, "source_app": window_name}`

- [x] **22.** In `listener.py` — implement `notify(title, message)` (port of `ui/notifications.ahk`):
  - Maintain `dict` keyed by `f"{title}|{message}"` with last-fire timestamp
  - Skip if last fire was < 5s ago (debounce)
  - Try `subprocess.run(["notify-send", title, message])` if D-Bus available
  - Fall back to `print(f"[{title}] {message}", file=sys.stderr)`

- [x] **23.** In `listener.py` — implement `clipboard_watcher()` (port of grammarFix.ahk `ClipboardWatcher` + `lib/classify.ahk`):
  - Start background thread polling `pyperclip.paste()` every 1s
  - Skip if clipboard content unchanged (compare SHA256 hash)
  - Blocklist: skip if any blocked process name is active (read from `/proc/*/comm`; blocked list: KeePass, KeePassXC, Bitwarden, 1Password, LastPass)
  - Cooldown: skip if < 5s since last fire
  - Port `ClassifyClipboard()` as pure Python: URL regex, stack trace markers, code syntax heuristics
  - On classification match: call `notify()` with hint message "URL detected — use summarize:" etc.
  - On/off state: presence/absence of `paths.MARKER_CLIPBOARD_WATCHER` file

- [x] **24.** In `listener.py` — implement `dashboard_poll_loop()`:
  - Background thread polling `paths.MARKER_OPEN_DASHBOARD` file every 500ms
  - When marker file appears: launch `subprocess.Popen(["flowkey-tui"])`, delete marker file

- [x] **25.** In `listener.py` — implement `register_hotkeys_from_config()` (port of `lib/hotkeys.ahk`):
  - Read hotkey bindings from config file
  - Unregister any previously-registered keyboard listeners
  - Register new GlobalHotKeys / evdev listeners with the new combos
  - Watch `paths.CONFIG_FILE` mtime and re-register on change (background thread, 5s interval)

- [x] **26.** In `listener.py` — implement `main()` and `shutdown()`:
  - `main()`: parse `--parent-pid` argument, register signal handlers (SIGTERM, SIGINT), call `ensure_daemon_running()`, `register_hotkeys_from_config()`, start clipboard watcher (if marker exists), start dashboard poll loop, enter `threading.Event().wait()` sleep
  - `shutdown()`: kill child processes (daemon, chat) via process group SIGTERM, stop all background threads
  - Register `shutdown()` as `atexit` handler and signal handler

> **Port-source note:** The Phase 3 item descriptions above reference `.ahk` files (e.g., `lib/mode_prefix.ahk`, `lib/clipboard.ahk`) to document which original AutoHotkey file was ported for each function. All actual "AHK" terminology in source code has been renamed to neutral terms (`shortcut_to_ahk` → `shortcut_to_compact`, `_ahk_to_human` → `_shortcut_to_human`, `AHK syntax` → `Key notation`, etc.) — zero AHK references remain in the codebase.

---

## Phase 4 — Textual TUI (replaces tkinter chat + dashboard)

The tkinter-based chat popup (`chat_popup.py`) and dashboard (`dashboard.py`) are replaced by a **Textual** TUI application. The TUI provides a modern terminal-based interface with:
- Streaming markdown chat (like opencode/claudecode)
- Multi-panel dashboard with live data from the daemon
- Keyboard-driven navigation and command palette
- Same daemon HTTP API as the replaced components

- [x] **27.** Create `scripts/tray.py` — simplified system tray indicator (2026-06-06)
  - Uses pystray on X11, graceful fallback on Wayland
  - Menu: Open TUI, Server submenu (Status/Start/Stop/Warmup), Performance toggles, Exit
  - Icon from `scripts/assets/flowkey.png` (256x256 RGBA)

- [x] **28.** Create `scripts/tui/__init__.py` — TUI package init (2026-06-06)

- [x] **29.** Create `scripts/tui/app.py` — Textual TUI entry point (2026-06-06):
  - Tabbed layout: Chat (primary), Dashboard
  - Keyboard-driven: Ctrl+1/2 for tabs, Ctrl+P command palette, Ctrl+Q quit
  - Connects to daemon at `127.0.0.1:52650` for data
  - Accepts `--parent-pid` argument for lifecycle tracking
  - `main()` entry point registered as `flowkey-tui` console script

- [x] **30.** Create `scripts/tui/chat.py` — Chat interface (replaces `chat_popup.py`) (2026-06-06):
  - Streaming markdown responses via SSE from local LLM
  - Message history with role indicators (user/assistant)
  - Slash-command support: `/grammar`, `/summarize`, `/explain`, `/prompt`, `/tone`, `/clear`, `/help`
  - Non-streaming mode for grammar-fix subprocess calls
  - Mode prefix parsing (same logic as `listener.py`)

- [x] **31.** Create `scripts/tui/dashboard.py` — Dashboard panels (replaces `dashboard.py`) (2026-06-06):
  - Data fetched via HTTP POST to daemon actions
  - **Overview panel:** daemon status, model, version, activity counters, hotkeys
  - **Telemetry panel:** counters by mode, latency percentiles, tokens, tok/s
  - **History panel:** recent 50 entries from JSONL
  - **Notes panel:** vault directory, categories
  - **Config panel:** hotkeys, FLM URL, model, performance mode
  - **Benchmark panel:** status + recent results table

- [x] **32.** Deprecate `chat_popup.py` and `dashboard.py` (2026-06-06):
  - Added deprecation warning to stdout on launch
  - Marked for removal in next major version
  - File stubs kept for backward compatibility

- [x] **33.** Update `pyproject.toml` (2026-06-06):
  - Added `textual>=8.0` to core dependencies
  - Fixed `python-evdev>=1.7` → `evdev>=1.7` (PyPI name)
  - Added `packages = { find = { where = ["scripts"], include = ["tui*"] } }` for tui package
  - Registered `flowkey-tui = "tui.app:main"` console script

> **venv** : Set up venv at `venv/` to isolate project dependencies from system packages. **Note for installer / CI:** The `install.sh` in Phase 5 should consider whether to create a venv or install system-wide. For development, always use the venv. For production distribution, system packages may be preferred (see `install.sh` design).


### Phase 4 follow-up — bug fixes

- [x] **33a.** `MessageBubble` content invisible at runtime (2026-06-06):
  - Root cause: `on_mount` called `update()` after layout, but content was invisible (possibly zero-height during initial layout or `$primary`/`$secondary` markup colors don't resolve in Rich's inline parser)
  - Fix: refactored to pass formatted content to `super().__init__()` for correct initial height; replaced `_render_content()` with `_format_content()` returning string; removed `$primary`/`$secondary` from markup since theme variables aren't available in Rich markup context
  - `update_content()` and `finalize_stream()` now call `self.update()` directly

- [x] **33b.** Dashboard tabs stuck on "Loading..." + other TUI runtime errors (2026-06-06):
  - `call_from_thread` doesn't exist in Textual 8.x — replaced with `call_later` everywhere
  - Dashboard fetchers only rendered on success; added unconditional `call_later` + error-state rendering
  - First load now synchronous (`_fetch_all_sync` in `on_mount`) so data appears immediately
  - Ctrl+P crash from recursive `action_command_palette` (called itself) — removed the override, uses built-in
  - `flowkey.http` WARNING logs muted to ERROR in TUI to prevent stderr overlap
  - Removed `Header(show_clock=True)` — user found it unnecessary
  - `MessageBubble` escaping only applied to user role, not assistant (was destroying `[b]`/`[i]` in HELP_TEXT)

---

## Phase 5 — Installer & Distribution

- [x] **34.** Create `install.sh`:
  - Detect Linux distribution (apt vs rpm)
  - Install system dependencies: `python3-pyperclip`, `python3-pynput` (X11), `python3-evdev` (Wayland), `python3-pystray` (X11), `wl-clipboard` (Wayland), `xdotool` (X11), `ydotool` (Wayland paste-back), `libnotify-bin` (notify-send), `python3-textual` (TUI)
  - Add user to `input` group for evdev access
  - Write udev rule to `/etc/udev/rules.d/99-flowkey-listener.rules`: `KERNEL=="event*", GROUP="input", MODE="0660"`
  - Run `pip install .`
  - Create XDG autostart `.desktop` file at `~/.config/autostart/flowkey-listener.desktop` pointing to `flowkey-listener`
  - Create `~/.local/share/applications/flowkey-tui.desktop` for TUI app menu entry
  - Convert `assets/flowkey.ico` to `.png` if ImageMagick available

- [x] **35.** Create `.github/workflows/ci.yml` — Linux CI:
  - `ubuntu-latest` runner
  - Install system deps: `python3-dev`, `libx11-dev`, `libevdev-dev`
  - `pip install .[dev]`
  - `ruff check scripts tests`
  - `pytest tests -q`
  - (Python tests only; AHK tests removed)

---

## Phase 6 — Cleanup & Documentation

- [x] **36.** Update `.gitignore`: clean for Linux, remove Windows artifacts

- [x] **37.** Update `CHANGELOG.md` — note Linux port initial version

- [x] **38.** Remove remaining Windows-only seed config duplicates in `setup/defaults/` if any are Windows-specific — vault_dir updated to Linux path

- [x] **39.** Final `pytest tests/ -q` pass on Linux — **81 passed, 2 skipped** (tkinter not available in headless CI)

- [ ] **40.** Update `README.md` with Linux-specific install instructions, dependencies, troubleshooting, and TUI usage guide

---

## Development Notes

