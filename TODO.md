# Flowkey Linux Port — Work Log

This document tracks the complete Linux port of [Fastflow/Flowkey](https://github.com/agr77one/Fastflow) to X11 and Wayland systems.

**Original repo (reference):** `~/devel/Fastflow` (cloned from `agr77one/Fastflow`)

---

## How to use this document

Each item is a single self-contained change. Items are in chronological order. Items with `[x]` are done. When you complete an item, mark it `[x]` and add the date + commit hash.

---

## Phase 1 — Python Backend Port

- [ ] **1.** Update `paths.py` — detect Linux (`$XDG_DATA_HOME` / `~/.local/share`) instead of `%LOCALAPPDATA%`; replace `_is_under_program_files()` with `_is_under_prefix()` checking `/usr`, `/usr/local`, `/opt`

- [ ] **2.** Update `subprocess_util.py` — gate `CREATE_NO_WINDOW` behind `sys.platform == "win32"`, add Linux-compatible `run_hidden` / `popen_hidden` that passes through without Windows-specific flags

- [ ] **3.** Rewrite `ffp_flm_server.py` — replace `tasklist` / `taskkill` / `netstat -ano` with Linux equivalents: `ps --pid`, `kill`, `ss -tlnp`

- [ ] **4.** Rewrite `ffp_notify.py` — replace PowerShell toast with `subprocess.run(["notify-send", title, message])`, fall back to print-to-stderr if D-Bus unavailable

- [ ] **5.** Replace `ffp_daemon.py` parent-PID watching — remove `ctypes.windll` WinAPI path, use Linux `PR_SET_PDEATHSIG` via `os.prctl(parent_death_signal=signal.SIGTERM)` or poll `/proc/<parent_pid>/status`

- [ ] **6.** Update `ffp_config.py` and `first_run.py` — replace `os.path.expandvars("%USERPROFILE%")` with `os.path.expandvars("$HOME")` for default vault path; remove NPU-detection Windows-Device-Manager path (Linux FLM has its own validation via `flm validate`)

- [ ] **7.** Run `pytest tests/ -q` on the updated Python backend, fix all platform-conditional test failures

---

## Phase 2 — Dependencies & Project Config

- [ ] **8.** Add new platform dependencies to `pyproject.toml` under `[project.optional-dependencies]`:
  - `pyperclip` — cross-platform clipboard access
  - `pynput; sys_platform == 'linux'` — X11 global hotkeys + key simulation
  - `python-evdev; sys_platform == 'linux'` — Wayland global hotkey capture (evdev)
  - `pystray; sys_platform == 'linux'` — system tray icon (X11; best-effort on Wayland)
  - `dasbus; sys_platform == 'linux'` — Wayland StatusNotifierItem D-Bus tray fallback

- [ ] **9.** Update `pyproject.toml`:
  - Add new modules to `py-modules`: `ffp_listener`, `ffp_tray`, `ffp_dashboard`
  - Add console_scripts: `ffp-listener = "ffp_listener:main"`, `ffp-tray = "ffp_tray:main"`, `ffp-dashboard = "ffp_dashboard:main"`
  - Add platform-conditional dependencies sections
  - Update classifiers to include `Operating System :: POSIX :: Linux`

---

## Phase 3 — Global Hotkey Listener (replaces AHK frontend)

- [ ] **10.** Create `scripts/ffp_listener.py` — module-level config loader:
  - `read_config()`: JSON load from `paths.CONFIG_FILE`, extract `hotkeys` object with keybindings, merge with defaults from `ffp_config.DEFAULT_CONFIG`
  - Store mode system prompts, FLM base URL, timeout values in module globals

- [ ] **11.** In `ffp_listener.py` — implement daemon lifecycle:
  - `ensure_daemon_running()`: hit `http://127.0.0.1:52650/healthz` with 0.5s timeout via `urllib.request`; if unreachable, spawn daemon via `subprocess.Popen(["ffp-daemon"])` and retry up to 10 times (5s total)

- [ ] **12.** In `ffp_listener.py` — implement daemon dispatch:
  - `dispatch_action(action, body_json)`: HTTP POST to `http://127.0.0.1:52650/action/<action>` with `X-FFP-API: 1` header and JSON body
  - On any HTTP failure, fall back to subprocess: `subprocess.run(["ffp-grammar-fix", "--app-action", action])`
  - Return parsed result string (parse daemon JSON response)

- [ ] **13.** In `ffp_listener.py` — implement mode prefix parsing (port of `lib/mode_prefix.ahk`):
  - `parse_mode_and_text(text)`: regex match first non-empty line for `prompt:`, `summarize:`, `explain:`, `tone:` prefixes
  - Support `:` separator, ` - ` separator, inline and multiline body extraction
  - Return `(mode, body_text)`; default mode is `grammar`

- [ ] **14.** In `ffp_listener.py` — implement clipboard helpers (port of `lib/clipboard.ahk`):
  - `clipboard_save()`: save current clipboard text via `pyperclip.paste()`
  - `clipboard_restore(text)`: `pyperclip.copy(text)`
  - `clipboard_capture()`: save clipboard, clear it (`pyperclip.copy("")`), simulate Ctrl+C (X11: `pynput.keyboard.Controller()`, Wayland: `xdotool key ctrl+c`), wait 200ms, read clipboard, restore original, return captured text

- [ ] **15.** In `ffp_listener.py` — implement `paste_back(text)` (the hardest subproblem):
  - Copy `text` to clipboard via `pyperclip.copy(text)`
  - Detect `$XDG_SESSION_TYPE`:
    - X11: simulate Ctrl+V via `pynput.keyboard.Controller().press(Key.ctrl)`, `.press(KeyCode.from_char('v'))`, `.release()`, `.release()`
    - Wayland: attempt `subprocess.run(["ydotool", "key", "ctrl+v"])` if `ydotool` in PATH; if unavailable, call `notify-send "Flowkey" "Result copied — press Ctrl+V to paste"` and skip paste-back

- [ ] **16.** In `ffp_listener.py` — implement keyboard capture init:
  - Detect `$XDG_SESSION_TYPE` at module load
  - If `x11`: start `pynput.keyboard.GlobalHotKeys` listener with combos from config
  - If `wayland`: open `/dev/input/event*` via `evdev.InputDevice`, match keycodes against configured combos
  - If neither: log error and exit

- [ ] **17.** In `ffp_listener.py` — implement `process_selection()` (the grammar/prompt/summarize/explain/tone hotkey handler):
  - Call `clipboard_capture()` to get selected text
  - Call `parse_mode_and_text()` to extract mode prefix from body
  - Write body text to temp file via `tempfile.NamedTemporaryFile`
  - Run `subprocess.run(["ffp-grammar-fix", "--mode", mode, "--input-file", infile, "--output-file", outfile])` with timeout from config
  - Read result from output temp file
  - Call `paste_back(result)`
  - Update telemetry counters

- [ ] **18.** In `ffp_listener.py` — implement `capture_note()` (Ctrl+Alt+N handler):
  - Call `clipboard_capture()` to get selection
  - If fresh capture is empty, fall back to existing clipboard content
  - Get active window name: X11 via `subprocess.run(["xdotool", "getactivewindow", "getwindowname"])` or via `python-xlib`; Wayland: best-effort via `swaymsg -t get_tree` or empty string
  - POST to daemon action `save_note` with `{"text": captured, "source_app": window_name}`
  - Call `notify("Note saved")`

- [ ] **19.** In `ffp_listener.py` — implement `launch_chat()` (Ctrl+Shift+T handler):
  - POST `chat_restart` action to daemon
  - Wait 200ms
  - Spawn `subprocess.Popen(["ffp-chat"])`

- [ ] **20.** In `ffp_listener.py` — implement `ask_with_selection()` (Ctrl+Shift+A handler):
  - Call `clipboard_capture()` to get selection
  - Get active window name (same as step 18)
  - POST to daemon action `chat_send_selection` with `{"text": captured, "source_app": window_name}`

- [ ] **21.** In `ffp_listener.py` — implement `notify(title, message)` (port of `ui/notifications.ahk`):
  - Maintain `dict` keyed by `f"{title}|{message}"` with last-fire timestamp
  - Skip if last fire was < 5s ago (debounce)
  - Try `subprocess.run(["notify-send", title, message])` if D-Bus available
  - Fall back to `print(f"[{title}] {message}", file=sys.stderr)`

- [ ] **22.** In `ffp_listener.py` — implement `clipboard_watcher()` (port of grammarFix.ahk `ClipboardWatcher` + `lib/classify.ahk`):
  - Start background thread polling `pyperclip.paste()` every 1s
  - Skip if clipboard content unchanged (compare SHA256 hash)
  - Blocklist: skip if any blocked process name is active (read from `/proc/*/comm`; blocked list: KeePass, KeePassXC, Bitwarden, 1Password, LastPass)
  - Cooldown: skip if < 5s since last fire
  - Port `ClassifyClipboard()` as pure Python: URL regex, stack trace markers, code syntax heuristics
  - On classification match: call `notify()` with hint message "URL detected — use summarize:" etc.
  - On/off state: presence/absence of `paths.MARKER_CLIPBOARD_WATCHER` file

- [ ] **23.** In `ffp_listener.py` — implement `dashboard_poll_loop()`:
  - Background thread polling `paths.MARKER_OPEN_DASHBOARD` file every 500ms
  - When marker file appears: launch `subprocess.Popen(["ffp-dashboard"])`, delete marker file

- [ ] **24.** In `ffp_listener.py` — implement `register_hotkeys_from_config()` (port of `lib/hotkeys.ahk`):
  - Read hotkey bindings from config file
  - Unregister any previously-registered keyboard listeners
  - Register new GlobalHotKeys / evdev listeners with the new combos
  - Watch `paths.CONFIG_FILE` mtime and re-register on change (background thread, 5s interval)

- [ ] **25.** In `ffp_listener.py` — implement `main()` and `shutdown()`:
  - `main()`: parse `--parent-pid` argument, register signal handlers (SIGTERM, SIGINT), call `ensure_daemon_running()`, `register_hotkeys_from_config()`, start clipboard watcher (if marker exists), start dashboard poll loop, enter `threading.Event().wait()` sleep
  - `shutdown()`: kill child processes (daemon, chat) via process group SIGTERM, stop all background threads
  - Register `shutdown()` as `atexit` handler and signal handler

---

## Phase 4 — Tray & Dashboard GUI

- [ ] **26.** Create `scripts/ffp_tray.py` — system tray menu:
  - Import `pystray` on X11, `dasbus` StatusNotifierItem on Wayland
  - If both fail (e.g. GNOME Wayland): log warning, exit silently — dashboard remains accessible via hotkey
  - Build menu: Open Chat, Dashboard, separator, Toggles submenu (Performance, Tone, History, Start at login, Clipboard watcher), Server submenu (Warmup, Stop, Check for updates), separator, Exit
  - Each menu item dispatches via HTTP POST to daemon at `127.0.0.1:52650/action/<name>` (same as listener)
  - Set tray icon from `assets/flowkey.ico` (convert to `.png` for Linux)
  - Refresh checkmarks on config change (poll every 5s)

- [ ] **27.** Create `scripts/ffp_dashboard.py` — tkinter port of `ui/dashboard.ahk` + `ui/dashboard_handlers.ahk` (1,347 lines combined):
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

- [ ] **28.** Create `install.sh`:
  - Detect Linux distribution (apt vs rpm)
  - Install system dependencies: `python3-pyperclip`, `python3-pynput`, `python3-evdev`, `python3-pystray`, `wl-clipboard` (Wayland), `xdotool` (X11), `ydotool` (Wayland paste-back), `libnotify-bin` (notify-send)
  - Add user to `input` group for evdev access
  - Write udev rule to `/etc/udev/rules.d/99-ffp-listener.rules`: `KERNEL=="event*", GROUP="input", MODE="0660"`
  - Run `pip install .`
  - Create XDG autostart `.desktop` file at `~/.config/autostart/ffp-listener.desktop` pointing to `ffp-listener`
  - Create `~/.local/share/applications/ffp-listener.desktop` for app menu entry
  - Convert `assets/flowkey.ico` to `.png` if IconMagick available

- [ ] **29.** Create `.github/workflows/ci.yml` — Linux CI:
  - `ubuntu-latest` runner
  - Install system deps: `python3-dev`, `libx11-dev`, `libevdev-dev`
  - `pip install .[dev]`
  - `ruff check scripts tests`
  - `pytest tests -q`
  - (Python tests only; AHK tests removed)

---

## Phase 6 — Cleanup & Documentation

- [ ] **30.** Update `.gitignore`: add Linux artifacts (`*.pyc`, `__pycache__/`, `*.egg-info/`, `dist/`, `build/`, `.venv/`)

- [ ] **31.** Update `CHANGELOG.md` — note Linux port initial version

- [ ] **32.** Remove remaining Windows-only seed config duplicates in `setup/defaults/` if any are Windows-specific (verify against cross-platform config format)

- [ ] **33.** Final `pytest tests/ -q` pass on Linux — all tests green

- [ ] **34.** Update `README.md` with Linux-specific install instructions, dependencies, troubleshooting
