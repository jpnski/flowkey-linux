# Flowkey Linux Port — Work Log

This document tracks the complete Linux port of [Fastflow/Flowkey](https://github.com/agr77one/Fastflow) to X11 and Wayland systems.

**Original repo (reference):** `~/devel/Fastflow` (cloned from `agr77one/Fastflow`)

---

## How to use this document

Each item is a single self-contained change. Items are in chronological order. Items with `[x]` are done. When you complete an item, mark it `[x]` and add the date + commit hash.

**Editing convention for new items (LLM instructions):** Append new items at the end of the chronological list (just before `## Development Notes`), not between earlier items. This keeps the file's chronological order stable.

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

- [x] **35.** Update `.gitignore`: clean for Linux, remove Windows artifacts

- [x] **36.** Remove remaining Windows-only seed config duplicates in `setup/defaults/` if any are Windows-specific — vault_dir updated to Linux path

- [x] **37.** Final `pytest tests/ -q` pass on Linux

- [x] **39.** Update `README.md` with Linux-specific install instructions, dependencies, troubleshooting, and TUI usage guide

---

## Phase 7 — Codebase Refactoring & Distribution

### Naming & File Organization

- [x] **40.** Rename `scripts/grammar_fix.py` → `scripts/engine.py` — this 932-line module is the central LLM engine (server lifecycle, 6 processing modes, config, health checks, update checks), not a grammar-only tool. Update all imports in `daemon.py`, `notes.py`, listener.py, test files, and `pyproject.toml` references. (14 pre-existing test failures unchanged)

- [x] **41.** Delete `scripts/actions.py` and relocate its contents — `PULL_MODEL_TIMEOUT_SECONDS` → `config.json` (`flm_server.pull_timeout_seconds: 900`) and `READ_ONLY_SUBPROCESS_ACTIONS` → `daemon.py`. Both call sites (daemon.py, engine.py) read from config. listener.py imports from daemon. File removed from repo.

- [x] **42.** Remove `sys.path.insert(0, str(HERE))` hack from `daemon.py` — Python adds `scripts/` to `sys.path[0]` automatically when running `daemon.py` directly. Moved sibling imports (`engine`, `notify`, `config`) to top of module, removed `# noqa: E402` comments.

- [x] **43.** Clean up `subprocess_util.py` — removed dead `NO_WINDOW = 0` (unreferenced), removed dead `popen_hidden()` (no-op pass-through, unreferenced), renamed `run_hidden()` → `run_captured()` (no "hidden" behavior on Linux). Updated 5 call sites across `flm_server.py`, `benchmark.py`, and test monkeypatches in `test_flm_server.py`.

- [x] **44.** Replace hand-rolled `notify.xml_escape()` with stdlib `xml.sax.saxutils.escape()`. Delete dead `_xml_escape` wrapper from `daemon.py` and its test in `test_daemon.py`. Keep `xml_escape` in `notify.py` (it's a notification helper; stdlib + newline strip is the correct implementation).

### Bug Fixes (CRITICAL)

- [x] **45.** Fix logging hierarchy mismatch across 21 modules — `_setup_logging()` in `daemon.py` configured handlers on `logging.getLogger("ffp")` but every module uses `logging.getLogger("flowkey.*")`. These are separate hierarchies, so all log statements were silently discarded. Fixed: change `"ffp"` → `"flowkey"` so all `flowkey.*` loggers propagate to the handler-equipped ancestor.

- [x] **46.** Fix `dict_restore` index-ordering bug in `llm_client.py` — placeholder `__FFPDICT1__` is a substring of `__FFPDICT10__`, so iterating dict in insertion order corrupts restored text when >=10 words are protected. Fix: sort placeholders by length descending before replacing.

- [x] **47.** Fix stale PID in autostart file (`daemon.py` line 151) — `Exec=flowkey-listener --parent-pid {os.getpid()}` embedded the daemon PID at file-write time, which is stale after reboot. Parent-watching silently does nothing. Removed `--parent-pid` from autostart entry entirely — autostart is a system-level lifecycle, not a daemon-spawned child.

- [x] **48.** Fix TOCTOU race and timer state corruption in `pull.py` — moved `_proc = None` inside the lock in `_default_runner()` so `cancel_pull()` can't read a stale `_proc` pointer. Added `_reset_timer` global + `_cancel_reset_timer()`, called at start of both `start_pull()` and `_schedule_reset()`, so a stale 10s reset from a previous pull can't clobber an in-flight pull.

- [x] **49.** Fix `_finalize_stream` history update bug in `chat.py` line 719 — `self._history[-1]` is the same object as `last`, so the condition was always True and every message content (including user messages) was overwritten on finalize. Changed to `last["role"] == "assistant"`.

- [x] **50.** Fix `bool("false")` → `True` in `daemon.py` — `bool(args.get("enabled"))` treated any non-empty string as True, including `"false"`. Replaced with proper parsing: if already bool, pass through; otherwise check `str(raw or "").lower() in ("1", "true", "yes")`.

- [x] **51.** Fix `_shortcut_to_evdev` potential `NameError` in `listener.py` — variable `i` was only assigned in the `else` (compact format) branch, but line 1284 read `shortcut[i:]` unconditionally. Any hotkey using the human-readable `+` format (e.g., `ctrl+alt+g`) would raise a `NameError`. Fixed: moved the `i`-dependent line into the `else` block where `i` is defined.

### Systemic Anti-patterns

- [x] **52.** Eliminate bare `except Exception: pass` epidemic (~72 occurrences across codebase). Replaced with `log.debug()`/`log.warning()` and contextual messages across 15 files. Fixed TUI stderr pollution (`flowkey.propagate = False`). Downgraded daemon HTTP/action/spawn logging to DEBUG to reduce daemon.log noise.

- [x] **53.** Replace dict-as-config with typed dataclasses — defined `FlowkeyConfig`, `FlmApiConfig`, `FlmServerConfig`, `ChatConfig`, `NotesConfig`, `HotkeysConfig`, `HistoryConfig`, `InputProcessingConfig`, `UpdateConfig`, `StandardModeConfig`, `ToneModeConfig`, `ModeTonePresetsConfig`, `MaxTokens` in `config.py`. Replaced `DEFAULT_CONFIG` dict with `FlowkeyConfig()` default. Updated `load_config()`/`save_config()` signatures and all ~40 call sites across `engine.py`, `daemon.py`, `listener.py`, `notes.py`, `tui/app.py`. Updated tests for typed access. (14 pre-existing failures unchanged)

- [x] **54.** Consolidate `DAEMON_BASE_URL` (5 copies across `app.py`, `chat.py`, `tray.py`, `_daemon.py`, `listener.py`) and `_daemon_post` (3 implementations in `tray.py`, `_daemon.py`, `listener.py`) into a single shared module.

- [x] **55.** Fix module boundary violations — `daemon.py` calls `grammar_fix._warmup_request()` and `grammar_fix._flm_list()` (both private API, lines 184/271/275). `notes.py` calls `grammar_fix._call_flm_api()`, reads `grammar_fix.FLM_MODEL` and `grammar_fix.FLM_TIMEOUT_SECONDS` (lines 304-306). Either promote these to public API or route through existing public wrappers.

- [x] **56.** Eliminate local-import circular-dependency hacks — 9+ occurrences of `from tui.dashboard import DashboardWidget` and `from tui.chat import ChatWidget` inside function bodies across dashboard config_pane files. Restructure module dependency graph or add an event bus / signals layer.

### God Function & God Object Decomposition

- [x] **57.** Decompose `call_flm()` in `llm_client.py` — extracted `resolve_system_prompt()`, `ensure_server_running()`, `_process_grammar_chunks()`, `_compress_prompt_chunks()`, `_anti_echo_retry()`, `_rescue_prompt_quality()`. `call_flm()` shrunk from 181 lines to 58 lines. Split `_process_long_input()` into two mode-specific functions (grammar chunks vs prompt compression) with dispatch at the call site.

- [x] **58.** Decompose `process_selection()` in `listener.py` — extracted `_write_temp_input()`, `_create_temp_output()`, `_run_mode_subprocess()`, `_notify_mode_complete()`, `_cleanup_temp()`. Main function shrunk from 100 to 35 lines. Temp file boilerplate and subprocess error handling each isolated in single-responsibility helpers.

- [x] **59.** Decompose `_run_x11()` in `tray.py` — eliminated 9 nested functions. Replaced with single `_daemon_action()` helper + module-level `_power_mode` state + `_build_x11_menu()` + `_on_exit()`. `_run_x11()` shrunk from 120 lines to 20 lines (icon setup + run). Power mode updates trigger menu rebuild via `_x11_icon` global.

- [x] **60.** Decompose `ChatWidget` in `chat.py` — extracted `ChatHistory` class (thread-safe history with add/trim/clear/payload methods). Removed `_msg_seq`, `_lock`, `_thread_id` from ChatWidget. History access now goes through typed methods instead of raw list manipulation. Removed unused `uuid` import.

- [ ] **61.** Decompose `FlmModelPanel` in `flm.py` (33 instance attributes, worst in codebase). Split model listing, download/pull, version check, and config state into separate focused classes.

- [ ] **62.** Decompose `main()` functions — `listener.py` main (87 lines, 8+ things), `daemon.py` main (busy-wait), `install.py` main (41 lines, violates SRP), `notes.py` `_categorize_in_background` (59 lines).

### Cross-File Consolidation

- [ ] **63.** Extract shared `BackgroundJob` abstraction from `benchmark.py`/`pull.py` — both have identical patterns: `_lock`, `_job: dict`, `_thread`, `_update(**fields)`, `status()`. ~50 lines of boilerplate each.

- [x] **64.** Consolidate power mode strings into `PowerMode(str, Enum)` — defined in `config.py`, used across 7 files. Removed `PERF_TO_PMODE` identity dict from `flm_server.py`, replaced `_PERF_CYCLE` list with `list(PowerMode)`, consolidated 4 `set_power_*` action handlers into a single factory in `daemon.py`, simplified power-mode action dispatch in `engine.py`. Validation via `PowerMode(mode)` coercion instead of hardcoded sets. Tests pass, JSON roundtrip verified.

- [x] **65.** Replaced `filter_config_patch` 8 identical elif branches with a data-driven loop over `_PATCH_SECTION_KEYS` dict. Added `_PATCH_SECTION_KEYS: dict[str, frozenset[str]]` mapping section names to their allowed-key frozensets. Special `modes` handler retained as-is.

- [x] **66.** Removed `_WRITE_ACTIONS` duplicate action registry — replaced with `@_write_action` decorator on handler functions. `_WRITE_ACTIONS` is now derived from `ACTIONS` dict via `getattr(handler, '_write_action', False)`. Converted 3 tone lambda entries to named functions with `@_write_action`. Added `_write_action` attribute to factory-produced `_handler` closure in `_act_set_power_mode`. Single source of truth: tag or remove the decorator.

- [x] **67.** Extracted `read_history(path) -> list[dict]` helper in `telemetry.py` — consolidates JSONL open/parse/skip logic used by both `compute_usage_stats()` and `compute_dashboard_data()`. Removed ~15 lines of duplicated boilerplate (file-exists check, try/except file-open, strip/skip-empty, parse-JSON/skip-unparseable).

### Threading & Async

- [ ] **68.** Fix thread accumulation in dashboard refreshes (`dashboard/__init__.py` lines 127-128) — spawns a `threading.Thread` per pane on every refresh interval (10s) with no pool or throttling. Unbounded thread growth if daemon is slow. Use `ThreadPoolExecutor` with bounded `max_workers`.

- [ ] **69.** Switch from raw `threading.Thread` spawns to Textual Workers in TUI — `chat.py` lines 502/583, `dashboard/__init__.py`. Raw threads bypass Textual's worker lifecycle: can't be cancelled, don't participate in app shutdown, errors fly silently.

- [ ] **70.** Fix temp-file subprocess IPC in `chat.py` `_run_grammar_fix` (lines 504-546) — creates 2 temp files per invocation, writes text to disk, spawns subprocess, reads output file, deletes both. Use `stdin=subprocess.PIPE` / `stdout=subprocess.PIPE` instead.

### Dead Code Removal

- [x] **71.** Remove `PERF_TO_PMODE` identity dict in `flm_server.py` — done as part of item 64 (PowerMode enum).

- [x] **72.** Removed dead code: `_daemon_available` field + 4 writes from `chat.py` (assigned but never read); removed `chat_with_tools()`, `TOOLS`, `NOTE_SEARCH_TOOL`, `MAX_ROUNDS` from `tools.py` (blocked by upstream bug, no callers); `_xml_escape`/`bench_status`/`_trigger_bench` were already absent.

- [x] **73.** Dead code (`NO_WINDOW`, `popen_hidden`, `run_hidden`) already removed in item 43. Remaining `run_captured()` is a clean 16-line wrapper used by 4 call sites in 2 files, with test monkeypatching depending on it as a module-level seam. No further action needed.

### Minor Cleanups

- [x] **74.** Replaced manual BOM stripping (`raw_body[3:]` after `b"\xef\xbb\xbf"` check) with `raw_body.decode("utf-8-sig")` which handles BOM natively.

- [x] **75.** Replaced fragile `str.replace("http://", "").replace("https://", "")` in `flm_host_port()` with `urllib.parse.urlparse`. Handles IPv6, ports, paths correctly.

- [x] **76.** Replaced single `ss -tlnp` PID finder with two-tier approach: `_pids_via_proc_net()` reads `/proc/net/tcp` + `/proc/[pid]/fd` (no root, stable ABI), falls back to `_pids_via_ss()` if no PIDs found. Both return `[]` gracefully on error.

- [x] **77.** Replaced daemon main loop (`while not _shutdown_event.is_set(): server.handle_request()` with 1s polling) with `server.serve_forever()` on a background thread + `_shutdown_event.wait()` on main. Shutdown is via `server.shutdown()` instead of relying on timeout wake. Eliminates 1s idle polling wake.

- [x] **78.** Fix `notify.xml_escape` reimplementing stdlib — done as part of item 44 (Replaced hand-rolled xml_escape with stdlib xml.sax.saxutils.escape).

- [x] **79.** Replaced hand-rolled `_percentile` linear interpolation with `statistics.quantiles(data, n=100, method="inclusive")`. Results match previous implementation (<1e-12 difference). Added `import statistics`.

- [x] **80.** Replaced hand-rolled `version_tuple` (stripped non-digits, dropped pre-release) with `packaging.version.Version`. Now `"1.2.3-beta1" < "1.2.3"` as expected. Added `from packaging.version import Version`.

### Distribution

- [x] **81.** Add PyInstaller frozen-mode detection to `paths.py` — when `getattr(sys, 'frozen', False)` is set, treat as production layout (runtime data → `~/.local/share/Flowkey/`, not temp bundle dir).

- [ ] **82.** Consolidate 6 console_scripts into single `flowkey` binary with subcommands (`daemon`, `tui`, `listen`, `tray`, `install`, `process`).

- [ ] **83.** Add a PyInstaller packaging spec / build entrypoint for the `flowkey` binary — bundle the app resources, make the single-command dispatcher the primary executable, and ensure frozen builds preserve the current module layout.

- [ ] **84.** Update all installed-launch surfaces to target `flowkey` subcommands instead of separate console scripts — desktop entries, autostart, installer post-install steps, and any internal spawns should use `flowkey daemon`, `flowkey tui`, `flowkey listen`, `flowkey tray`, etc.

- [ ] **85.** Replace pip-install distribution with PyInstaller-built binary — rewrite `install.sh` for curl-to-bash: detect arch, download GitHub release tarball, extract to PATH, run system setup (udev, groups, deps, desktop entry). Keep `--from-source` path for dev contributors.

- [ ] **86.** Add an install-time smoke test for the distributed binary — verify a clean user-local install can launch `flowkey daemon`, `flowkey tui`, and `flowkey listen` from PATH, and that runtime state lands in the XDG data dirs rather than the repo or bundle temp directory.

- [ ] **87.** Set up GitHub Actions release workflow — on tag push, build PyInstaller binaries for x86_64 + aarch64, upload as release assets.

- [ ] **88.** Rewrite README installation docs for the binary release flow — document the `curl -fsSL ... | bash` install path, `~/.local/bin` expectations, and the supported `--from-source` fallback for contributors.
