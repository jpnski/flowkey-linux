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

## Phase 3 — Global Hotkey Listener (replaces AHK frontend)

- [ ] **11.** Create `scripts/listener.py` — module-level config loader:
  - `read_config()`: JSON load from `paths.CONFIG_FILE`, extract `hotkeys` object with keybindings, merge with defaults from `config.DEFAULT_CONFIG`
  - Store mode system prompts, FLM base URL, timeout values in module globals

- [ ] **12.** In `listener.py` — implement daemon lifecycle:
  - `ensure_daemon_running()`: hit `http://127.0.0.1:52650/healthz` with 0.5s timeout via `urllib.request`; if unreachable, spawn daemon via `subprocess.Popen(["flowkey-daemon"])` and retry up to 10 times (5s total)

- [ ] **13.** In `listener.py` — implement daemon dispatch:
  - `dispatch_action(action, body_json)`: HTTP POST to `http://127.0.0.1:52650/action/<action>` with `X-FFP-API: 1` header and JSON body
  - On any HTTP failure, fall back to subprocess: `subprocess.run(["flowkey-grammar-fix", "--app-action", action])`
  - Return parsed result string (parse daemon JSON response)

- [ ] **14.** In `listener.py` — implement mode prefix parsing (port of `lib/mode_prefix.ahk`):
  - `parse_mode_and_text(text)`: regex match first non-empty line for `prompt:`, `summarize:`, `explain:`, `tone:` prefixes
  - Support `:` separator, ` - ` separator, inline and multiline body extraction
  - Return `(mode, body_text)`; default mode is `grammar`

- [ ] **15.** In `listener.py` — implement clipboard helpers (port of `lib/clipboard.ahk`):
  - `clipboard_save()`: save current clipboard text via `pyperclip.paste()`
  - `clipboard_restore(text)`: `pyperclip.copy(text)`
  - `clipboard_capture()`: save clipboard, clear it (`pyperclip.copy("")`), simulate Ctrl+C (X11: `pynput.keyboard.Controller()`, Wayland: `xdotool key ctrl+c`), wait 200ms, read clipboard, restore original, return captured text

- [ ] **16.** In `listener.py` — implement `paste_back(text)` (the hardest subproblem):
  - Copy `text` to clipboard via `pyperclip.copy(text)`
  - Detect `$XDG_SESSION_TYPE`:
    - X11: simulate Ctrl+V via `pynput.keyboard.Controller().press(Key.ctrl)`, `.press(KeyCode.from_char('v'))`, `.release()`, `.release()`
    - Wayland: attempt `subprocess.run(["ydotool", "key", "ctrl+v"])` if `ydotool` in PATH; if unavailable, call `notify-send "Flowkey" "Result copied — press Ctrl+V to paste"` and skip paste-back

- [ ] **17.** In `listener.py` — implement keyboard capture init:
  - Detect `$XDG_SESSION_TYPE` at module load
  - If `x11`: start `pynput.keyboard.GlobalHotKeys` listener with combos from config
  - If `wayland`: open `/dev/input/event*` via `evdev.InputDevice`, match keycodes against configured combos
  - If neither: log error and exit

- [ ] **18.** In `listener.py` — implement `process_selection()` (the grammar/prompt/summarize/explain/tone hotkey handler):
  - Call `clipboard_capture()` to get selected text
  - Call `parse_mode_and_text()` to extract mode prefix from body
  - Write body text to temp file via `tempfile.NamedTemporaryFile`
  - Run `subprocess.run(["flowkey-grammar-fix", "--mode", mode, "--input-file", infile, "--output-file", outfile])` with timeout from config
  - Read result from output temp file
  - Call `paste_back(result)`
  - Update telemetry counters

- [ ] **19.** In `listener.py` — implement `capture_note()` (Ctrl+Alt+N handler):
  - Call `clipboard_capture()` to get selection
  - If fresh capture is empty, fall back to existing clipboard content
  - Get active window name: X11 via `subprocess.run(["xdotool", "getactivewindow", "getwindowname"])` or via `python-xlib`; Wayland: best-effort via `swaymsg -t get_tree` or empty string
  - POST to daemon action `save_note` with `{"text": captured, "source_app": window_name}`
  - Call `notify("Note saved")`

- [ ] **20.** In `listener.py` — implement `launch_chat()` (Ctrl+Shift+T handler):
  - POST `chat_restart` action to daemon
  - Wait 200ms
  - Spawn `subprocess.Popen(["flowkey-chat"])`

- [ ] **21.** In `listener.py` — implement `ask_with_selection()` (Ctrl+Shift+A handler):
  - Call `clipboard_capture()` to get selection
  - Get active window name (same as step 18)
  - POST to daemon action `chat_send_selection` with `{"text": captured, "source_app": window_name}`

- [ ] **22.** In `listener.py` — implement `notify(title, message)` (port of `ui/notifications.ahk`):
  - Maintain `dict` keyed by `f"{title}|{message}"` with last-fire timestamp
  - Skip if last fire was < 5s ago (debounce)
  - Try `subprocess.run(["notify-send", title, message])` if D-Bus available
  - Fall back to `print(f"[{title}] {message}", file=sys.stderr)`

- [ ] **23.** In `listener.py` — implement `clipboard_watcher()` (port of grammarFix.ahk `ClipboardWatcher` + `lib/classify.ahk`):
  - Start background thread polling `pyperclip.paste()` every 1s
  - Skip if clipboard content unchanged (compare SHA256 hash)
  - Blocklist: skip if any blocked process name is active (read from `/proc/*/comm`; blocked list: KeePass, KeePassXC, Bitwarden, 1Password, LastPass)
  - Cooldown: skip if < 5s since last fire
  - Port `ClassifyClipboard()` as pure Python: URL regex, stack trace markers, code syntax heuristics
  - On classification match: call `notify()` with hint message "URL detected — use summarize:" etc.
  - On/off state: presence/absence of `paths.MARKER_CLIPBOARD_WATCHER` file

- [ ] **24.** In `listener.py` — implement `dashboard_poll_loop()`:
  - Background thread polling `paths.MARKER_OPEN_DASHBOARD` file every 500ms
  - When marker file appears: launch `subprocess.Popen(["flowkey-dashboard"])`, delete marker file

- [ ] **25.** In `listener.py` — implement `register_hotkeys_from_config()` (port of `lib/hotkeys.ahk`):
  - Read hotkey bindings from config file
  - Unregister any previously-registered keyboard listeners
  - Register new GlobalHotKeys / evdev listeners with the new combos
  - Watch `paths.CONFIG_FILE` mtime and re-register on change (background thread, 5s interval)

- [ ] **26.** In `listener.py` — implement `main()` and `shutdown()`:
  - `main()`: parse `--parent-pid` argument, register signal handlers (SIGTERM, SIGINT), call `ensure_daemon_running()`, `register_hotkeys_from_config()`, start clipboard watcher (if marker exists), start dashboard poll loop, enter `threading.Event().wait()` sleep
  - `shutdown()`: kill child processes (daemon, chat) via process group SIGTERM, stop all background threads
  - Register `shutdown()` as `atexit` handler and signal handler

---

## Phase 4 — Tray & Dashboard GUI

- [ ] **27.** Create `scripts/tray.py` — system tray menu:
  - Import `pystray` on X11, `dasbus` StatusNotifierItem on Wayland
  - If both fail (e.g. GNOME Wayland): log warning, exit silently — dashboard remains accessible via hotkey
  - Build menu: Open Chat, Dashboard, separator, Toggles submenu (Performance, Tone, History, Start at login, Clipboard watcher), Server submenu (Warmup, Stop, Check for updates), separator, Exit
  - Each menu item dispatches via HTTP POST to daemon at `127.0.0.1:52650/action/<name>` (same as listener)
  - Set tray icon from `assets/flowkey.ico` (convert to `.png` for Linux)
  - Refresh checkmarks on config change (poll every 5s)

- [ ] **28.** Create `scripts/dashboard.py` — tkinter port of `ui/dashboard.ahk` + `ui/dashboard_handlers.ahk` (1,347 lines combined):
  - 6 tabs: Overview, Telemetry, History, Notes, Config, Benchmark
  - Data fetched via HTTP POST to daemon actions: `config_snapshot`, `stats`, `dashboard_data`, `bench_history`, `models_installed`
  - **Overview tab:** daemon status, model, version, activity counters (total/grammar/prompt), preferences (performance/tone/history/vault), hotkey display
  - **Telemetry tab:** counters by mode, latency percentiles (P50/P90/P99), tokens, tok/s, time-of-day usage (basic plot via tkinter Canvas or matplotlib if available)
  - **History tab:** recent 50 entries from JSONL, scrollable list with timestamps
  - **Notes tab:** vault directory, categories list, LLM categorization toggle, generate title/summary toggles
  - **Config tab:** hotkey bindings (4 text inputs with validation), FLM base URL, model dropdown (populated from models_installed), performance mode radio, routing toggles, Save button → writes patch via daemon `apply_config_patch`
  - **Benchmark tab:** Run button → daemon `bench_start`, poll `bench_status`, show results table from `bench_history`
  - Accept `--parent-pid` argument for lifecycle tracking

---

## Phase 5 — Installer & Distribution

- [ ] **29.** Create `install.sh`:
  - Detect Linux distribution (apt vs rpm)
  - Install system dependencies: `python3-pyperclip`, `python3-pynput`, `python3-evdev`, `python3-pystray`, `wl-clipboard` (Wayland), `xdotool` (X11), `ydotool` (Wayland paste-back), `libnotify-bin` (notify-send)
  - Add user to `input` group for evdev access
  - Write udev rule to `/etc/udev/rules.d/99-flowkey-listener.rules`: `KERNEL=="event*", GROUP="input", MODE="0660"`
  - Run `pip install .`
  - Create XDG autostart `.desktop` file at `~/.config/autostart/flowkey-listener.desktop` pointing to `flowkey-listener`
  - Create `~/.local/share/applications/flowkey-listener.desktop` for app menu entry
  - Convert `assets/flowkey.ico` to `.png` if IconMagick available

- [ ] **30.** Create `.github/workflows/ci.yml` — Linux CI:
  - `ubuntu-latest` runner
  - Install system deps: `python3-dev`, `libx11-dev`, `libevdev-dev`
  - `pip install .[dev]`
  - `ruff check scripts tests`
  - `pytest tests -q`
  - (Python tests only; AHK tests removed)

---

## Phase 6 — Cleanup & Documentation

- [x] **31.** Update `.gitignore`: clean for Linux, remove Windows artifacts

- [x] **32.** Update `CHANGELOG.md` — note Linux port initial version

- [x] **33.** Remove remaining Windows-only seed config duplicates in `setup/defaults/` if any are Windows-specific — vault_dir updated to Linux path

- [x] **34.** Final `pytest tests/ -q` pass on Linux — **81 passed, 2 skipped** (tkinter not available in headless CI)

- [ ] **35.** Update `README.md` with Linux-specific install instructions, dependencies, troubleshooting
