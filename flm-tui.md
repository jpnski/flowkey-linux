# ffchat — Strip-Down Plan

Drop everything built for the Windows desktop-assistant paradigm. Keep only a **TUI chat for FastFlowLM** with **custom slash commands** replacing fixed modes. No daemon, no background processes, no installer CLI — `pip install . && ffchat`.

---

## Part 0: Deletions Summary

| File | Lines | Reason |
|---|---|---|
| `scripts/listener.py` | 298 | Hotkey listener — Windows paradigm, broken on Wayland |
| `scripts/tray.py` | 298 | System tray — no tray on Sway |
| `scripts/daemon.py` | 784 | Background HTTP server — only needed for multi-consumer access. Single TUI manages FLM directly. |
| `scripts/loopback_http.py` | 73 | HTTP IPC to daemon — daemon is gone |
| `scripts/tui/dashboard/_daemon.py` | 29 | Daemon communication helpers — daemon is gone |
| `scripts/tools.py` | 111 | Dead code — zero imports across the project |
| `scripts/notes.py` | 658 | Notes vault — separate product concern |
| `scripts/tui/dashboard/config_pane/hotkeys.py` | 206 | Hotkey editor UI — dead without listener |
| `scripts/tui/dashboard/config_pane/input_processing.py` | 286 | Chunking config — only used by old listener pipeline |
| `scripts/tui/dashboard/history.py` | 60 | Reads `grammar_fix_history.jsonl` — never written after strip |
| `scripts/tui/dashboard/notes.py` | 51 | Notes vault info pane — notes feature deleted |
| `tests/test_listener.py` | 246 | Listener tests |
| `tests/test_daemon.py` | ~300 | Daemon tests |
| `tests/test_notes.py` | ~30 | Notes tests |
| `tests/test_notes_search.py` | ~30 | Notes search tests |
| `tests/test_config_pane.py` | ~90 | Hotkeys panel tests |
| `tests/test_loopback_http.py` | ~50 | HTTP IPC tests |

**~3,600 lines of source + ~750 lines of tests removed.**

---

## Part 1: Config Schema — Strip Down

### 1a. Delete dataclasses from `scripts/config.py`

Remove:
- `TransformHotkeysConfig`, `InteractionHotkeysConfig`
- `StandardModeConfig`, `ToneModeConfig`, `ModeTonePresetsConfig`
- `MaxTokens`, `NotesConfig`, `InputProcessingConfig`
- `CLAUDE_PROMPT_SYSTEM_PROMPT` constant

Remove fields from `FlowkeyConfig`:
- `modes`, `transform_hotkeys`, `interaction_hotkeys`
- `grammar_ignore_words`, `terminal`, `notes`, `input_processing`

**Remaining dataclasses:** `PowerMode`, `FlmApiConfig`, `FlmServerConfig`, `HistoryConfig`, `ChatConfig`, `SlashCommand` (new), `FlowkeyConfig` (simplified).

### 1b. Add `SlashCommand` dataclass

```python
@dataclass
class SlashCommand:
    name: str = ""
    system_prompt: str = ""
    description: str = ""
    max_tokens: int = 512

# Add to FlowkeyConfig:
slash_commands: list[SlashCommand] = field(default_factory=list)
```

### 1c. Remove patch validation keys

Delete `_PATCH_TRANSFORM_HOTKEYS_KEYS`, `_PATCH_INTERACTION_HOTKEYS_KEYS`, `_PATCH_INPUT_PROCESSING_KEYS`, `_PATCH_NOTES_KEYS`, `_PATCH_TONE_KEYS`, `_PATCH_TOP_LEVEL_KEYS`.

From `_PATCH_SECTION_KEYS`, remove `"input_processing"`, `"transform_hotkeys"`, `"interaction_hotkeys"`, `"notes"`. Add `"slash_commands"`.

Remove the `"modes"` special-case handler from `filter_config_patch()`.

### 1d. Simplify seed config mechanism

Remove `_SEED_CONFIG` / `_seed_config_dict()` dual-loading. Replace with a simple `default_config()` inline. The merge complexity was needed for the 5-mode dict — with modes gone, defaults fit in a single dataclass factory.

### 1e. Update seed config

`scripts/config.seed.json` now contains: `theme`, `flm_api`, `flm_server`, `history`, `chat`, `slash_commands` (4 defaults: grammar, summarize, explain, prompt). No hotkeys, no modes, no notes, no input processing.

---

## Part 2: CLI — Strip to Two Subcommands

### 2a. `scripts/flowkey.py`

Remove `"listen"`, `"tray"`, `"daemon"`, `"process"` from `COMMANDS`. Remove all corresponding imports.

**Remaining:** `"tui"` and `"install"`.

### 2b. `scripts/engine.py` — gut from 921 lines to ~300

Delete:
- `list_hotkeys()`, `parse_mode()`, `get_tone_preset()`, `cycle_tone_preset()`
- `call_flm(mode, ...)` — the mode-dispatching pipeline
- `_resolve_token_budget()`, `_split_chunks()` — only used by `call_flm()`
- `handle_server_cli()` — `--app-action`/`--server` dispatch that duplicated daemon actions
- `main()`, `_cli_args()`, `_read_input_text()`, `_write_output_text()` — `flowkey process` CLI
- `grammar_ignore_words` references everywhere
- Notes section from `build_config_snapshot()`
- `call_flm_simple()` — only caller was notes.py, which is deleted
- `append_history()` — the TUI manages chat history internally in `chat_threads.jsonl`, no shared-history model needed

Keep:
- `load_config()`, `save_config()`, `refresh_runtime_config()`
- `start_flm_server()`, `stop_flm_server()`, `is_flm_server_reachable()`, `server_status()`, `warmup_request()`
- `get_power_mode()`, `set_power_mode()`, `toggle_power_mode()`
- `get_history_text_mode()`, `set_history_text_mode()`, `toggle_history_text_mode()`
- `compute_usage_stats()`, `compute_dashboard_data()`
- `build_config_snapshot()`, `apply_config_patch()`
- `list_flm_models()`, `run_doctor()`

These are called directly by the TUI (no HTTP indirection).

### 2c. `scripts/launcher.py` — delete or gut to ~10 lines

`flowkey_argv()` was for TUI subprocess mode transforms (gone). `flowkey_tui_argv()` was for the daemon's `chat_send_selection` (daemon is gone). The TUI doesn't need to resolve terminal emulators — it runs in the terminal that launched it.

**Either delete entirely** or leave a stub:
```python
"""Launcher helpers — all consumers removed. Kept for reference."""
```

---

## Part 3: LLM Pipeline — Strip Modes

### 3a. `scripts/llm_client.py` — gut from 473 lines to ~60

Delete everything mode-specific:
- `LlmRuntimeConfig.modes_cfg` and `.protected_words` fields
- `resolve_system_prompt()`, `resolve_token_budget()`, `is_prompt_mode()`
- `_anti_echo_retry()`, `_rescue_prompt_quality()`
- `_process_grammar_chunks()`, `_compress_prompt_chunks()`, `split_chunks()`
- `is_weak_prompt_echo()`, `looks_like_prompt_text()`, `force_prompt_shape()`, `strip_prompt_scaffold_labels()`
- `line_reuse_ratio()`, `word_set()`, `word_overlap_ratio()`
- `ensure_server_running()` — only caller was `call_flm()`
- The old `call_flm(mode, ...)` function — entire mode-dispatch pipeline

Keep:
- Simplified `LlmRuntimeConfig`: just `base_url`, `model`, `timeout_seconds`, `server_auto_start`
- `normalize_output()`, `dict_protect()`, `dict_restore()`
- `reset_usage_acc()`, `snapshot_usage_acc()`
- A simplified `call_flm(system_prompt, user_content, ...)` — no mode parameter:

```python
def call_flm(
    runtime: LlmRuntimeConfig,
    system_prompt: str,
    user_content: str,
    call_api: Callable,
    is_server_reachable: Callable,
    start_server: Callable,
    usage_acc: dict,
) -> tuple[str, float, str]:
    reset_usage_acc(usage_acc)
    started = time.time()
    if not is_server_reachable():
        if not runtime.server_auto_start:
            raise RuntimeError("FLM server is unreachable and auto_start=false.")
        start_server(False)
    text, model_used = call_api(runtime.model, system_prompt, user_content, 4096, runtime.timeout_seconds)
    return text, round(time.time() - started, 2), model_used
```

---

## Part 4: TUI — Only Consumer, Manages Everything Directly

The TUI was already the only real consumer. Now it's explicit — no HTTP layer, no daemon abstraction.

### 4a. `scripts/tui/chat.py`

Delete mode/hotkey/process remnants:
- `MODE_LABELS`, `SYSTEM_PROMPTS` dicts
- `_MODE_PREFIX_ENTRIES`, `_parse_mode_and_text()`
- `/grammar`, `/summarize`, `/explain`, `/prompt`, `/tone` branches in `_handle_slash_command()`
- `_handle_mode_transform()`, `_run_mode_transform()` — subprocess + temp file IPC
- `_get_last_selection()` — clipboard fallback for removed slash commands
- Mode-prefix detection branch in `_process_input()`
- `import loopback_http` — daemon is gone
- `import launcher`, `import subprocess`

Rewrite `_handle_slash_command()` to read from config-driven `_slash_commands` list:

```python
def _handle_slash_command(self, text: str) -> None:
    parts = text.split(maxsplit=1)
    cmd = parts[0].lower()
    args = parts[1] if len(parts) > 1 else ""
    if cmd == "/clear":
        self._clear_history()
    elif cmd == "/help":
        self._add_message("assistant", self._dynamic_help(), show_role=False)
    elif cmd.startswith("/"):
        name = cmd[1:]
        command = self._find_slash_command(name)
        if command:
            self._run_slash_command(command, args)
        else:
            self._add_message("assistant", f"Unknown: {cmd}\n\nType /help")
```

Rewrite config refresh to read `config.load_config()` directly instead of `daemon_post("config_snapshot")`:

```python
def _refresh_config(self) -> None:
    import config as _cfg
    cfg = _cfg.load_config()
    self._system_prompt = cfg.chat.system_prompt
    self._slash_commands = [asdict(c) for c in cfg.slash_commands]
```

Rewrite FLM server startup to call `engine.start_flm_server()` directly:

```python
def _ensure_server(self) -> None:
    import engine
    if not engine.is_flm_server_reachable():
        engine.start_flm_server()
```

### 4b. Dashboard — direct imports, no daemon calls

Replace every `_daemon_post(...)` call with a direct function call:

| Old (via daemon) | New (direct) |
|---|---|
| `_daemon_post("config_snapshot")` | `config.load_config()` + `engine.build_config_snapshot()` |
| `_daemon_post("models_installed")` | `engine.list_flm_models(installed=True)` |
| `_daemon_post("models_not_installed")` | `engine.list_flm_models(installed=False)` |
| `_daemon_post("stats")` | `engine.compute_usage_stats()` |
| `_daemon_post("bench_history")` | `benchmark.read_history()` |
| `_daemon_post("bench_status")` | `benchmark.status()` |
| `_daemon_post("bench_start", ...)` | `benchmark.start(...)` |
| `_daemon_post("apply_config_patch", ...)` | `engine.apply_config_patch(...)` |
| `_daemon_post("pull_status")` | `pull.status()` |
| `_daemon_post("pull_start", ...)` | `pull.start(...)` |
| `_daemon_post("pull_cancel")` | `pull.cancel()` |
| `_daemon_post("flm_update_check")` | `flm_server.check_flm_update()` |
| `_daemon_post("stop")` | `engine.stop_flm_server()` |

Each sub-panel imports the module it needs directly. No `_daemon.py` file, no `loopback_http` dependency.

### 4c. Dashboard — remove History, Notes, Hotkeys, InputProcessing

Delete:
- `scripts/tui/dashboard/config_pane/hotkeys.py`
- `scripts/tui/dashboard/config_pane/input_processing.py`
- `scripts/tui/dashboard/history.py`
- `scripts/tui/dashboard/notes.py`

From `scripts/tui/dashboard/__init__.py`:
- Remove NotesPane and HistoryPane imports and tab composition

From `scripts/tui/dashboard/config_pane/__init__.py`:
- Remove `HotkeysPanel` and `InputProcessingPanel` imports/composition
- Remove `_update_hotkeys_panel()` and `_update_input_processing_panel()` methods
- Remove hotkey/input-processing data gathering from `_fetch()`

### 4d. Add SlashCommandsPanel

New panel in Config tab. Simple CRUD editor for `slash_commands`:
- List with name + description + system_prompt preview
- Add / Edit / Delete buttons
- Persist via `engine.apply_config_patch({"slash_commands": [...]})`

### 4e. Telemetry pane

Remove `by_mode` display section (now meaningless). Keep total requests, latency, tokens, tok/s.

---

## Part 5: Paths — Remove Dead Constants

From `scripts/paths.py`:
- `COUNTERS_FILE`, `PROMPT_HISTORY_FILE`, `GRAMMAR_HISTORY_FILE`
- `MARKER_CLIPBOARD_WATCHER`, `MARKER_OPEN_DASHBOARD`
- `DAEMON_LOG_FILE`, `FLM_SERVER_LOG_FILE` (check if `flm_server.py` still references it)

---

## Part 6: Installer — Strip System Setup

### 6a. `scripts/install.py` — gut from 209 lines to ~50

Remove:
- `OPTIONAL_TOOLS` (xdotool, ydotool, wl-paste, wl-copy)
- `REQUIRED_GROUPS`, `_check_groups()`
- `_ensure_autostart()` — no daemon to autostart
- `_check_prereqs()` → inline `shutil.which("flm")` + `notify-send`
- Listener/daemon references from final messages

Result:
```python
"""Minimal installer: config bootstrap + model pull."""
def main(argv=None):
    parser = argparse.ArgumentParser(...)
    args = parser.parse_args(argv)
    _ensure_config()
    if _model_installed(args.model):
        print(f"Model {args.model} already installed.")
    else:
        if not _pull_model(args.model):
            return 1
    print("Setup complete. Run 'flowkey tui'.")
    return 0
```

### 6b. `install.sh` — gut from 557 lines to ~80

Remove:
- `install_system_packages()` — no system packages needed
- `setup_groups()`, `setup_udev()` — evdev dead
- `detect_distro()`, `run_privileged()` — no sudo needed
- All xdotool/ydotool/wl-clipboard/libevdev-dev/libX11-dev references
- Listener/daemon references from `print_summary()`
- Venv detection complexity in `run_source_install()`

Keep:
- Python version check (≥3.11)
- `notify-send` optional check
- Binary release download from GitHub
- Simplified `pip install .` source install
- `flowkey install` call
- Desktop entry creation (terminal for `flowkey tui`)
- Post-install summary (only mentions `flowkey tui`)

---

## Part 7: Build / Packaging

### 7a. `pyproject.toml`

Remove:
- `x11`, `wayland`, `tray`, `readability` optional-dependency groups
- `"listener"`, `"tray"`, `"notes"`, `"tools"`, `"daemon"` from `py-modules`
- `"Environment :: X11 Applications"`, `"Environment :: Wayland"` classifiers
- `"hotkey"` from keywords

Result:
```toml
dependencies = ["packaging>=23", "pyperclip>=1.8", "textual>=8.0"]
[project.optional-dependencies]
dev = ["build>=1.0", "pyinstaller>=6.0", "ruff>=0.4", "pytest>=7"]
```

**Zero optional runtime deps.** Three mandatory deps: `packaging`, `pyperclip`, `textual`.

### 7b. `flowkey.spec`

Remove `"listener"`, `"tray"`, `"notes"`, `"tools"`, `"daemon"` from `hiddenimports`.
Remove `collect_submodules` for `pynput`, `evdev`, `dasbus`, `pystray`.
Remove icon datas entries (no more tray icons).

### 7c. `build_frozen.py`

Unchanged. Still a thin PyInstaller wrapper.

### 7d. CI / release

No CI exists (no `.github/workflows/`). Releases are manual: `python -m build` (wheel), optionally `python scripts/build_frozen.py` (binary), manual GitHub Release upload. Simplest possible state for this stage.

---

## Part 8: Architecture After Strip-Down

```
ffchat
 └──→ Textual TUI
       imports engine, flm_server, config, pull, benchmark directly
       manages FLM lifecycle inline
       copies config seed on first launch (no install needed)
       TUI closes → FLM server stops → NPU memory freed
       │
       ├── Chat: streaming LLM, slash commands, turn history
       └── Dashboard: Config (model, server, chat, slash_cmds),
                      Telemetry (reqs, latency, tokens),
                      Benchmark (results table)
```

**Usage:**
```
pip install . && ffchat
```

**Process model:**
- One `ffchat` process
- One `flm` server child process (started/stopped by TUI)
- TUI closes → `flm` killed → NPU memory freed

**No background processes.** No daemon. No HTTP IPC. No installer CLI. No system packages. No sudo. No groups. No autostart.

**Remaining modules (10 + tui/):**

| Module | Lines | Purpose |
|---|---|---|
| `ffchat.py` | ~15 | CLI entry point (one subcommand: tui) |
| `engine.py` | ~300 | Server lifecycle, config CRUD, telemetry, model list |
| `config.py` | ~150 | 6 dataclasses, load/save/patch |
| `paths.py` | ~60 | File path constants |
| `version.py` | ~5 | Version string |
| `flm_server.py` | ~500 | FLM process lifecycle |
| `llm_client.py` | ~60 | Normalize, simple call_flm |
| `telemetry.py` | ~100 | JSONL history + stats |
| `pull.py` | ~270 | Async model download |
| `benchmark.py` | ~220 | Model benchmarking |
| `subprocess_util.py` | ~70 | FLM subprocess helpers |
| `notify.py` | ~45 | Desktop notifications (optional) |
| `tui/app.py` | ~240 | TUI main + screen |
| `tui/chat.py` | ~500 | Streaming chat + slash commands |
| `tui/dashboard/` | ~550 | 3 tabs, ~4 sub-panels |

**~3,000 source lines** (down from ~9,100), **one CLI command: `ffchat`** (down from 6 subcommands).

---

## Part 9: Tests

### Delete whole files

| File | Tests |
|---|---|
| `tests/test_listener.py` | 7 tests |
| `tests/test_daemon.py` | ~10 tests |
| `tests/test_config_pane.py` | 4 hotkey tests |
| `tests/test_notes.py` | 1 test |
| `tests/test_notes_search.py` | 2 tests |
| `tests/test_loopback_http.py` | ~3 tests |

### Update existing tests

| File | Changes |
|---|---|
| `tests/test_config.py` | Remove hotkey/mode fixture data; add slash_commands fixtures |
| `tests/test_engine.py` | Remove `test_list_hotkeys`; remove mode fixture data; update call sites |
| `tests/test_flowkey.py` | Remove process/daemon subcommand tests |
| `tests/test_launcher.py` | Remove or update if launcher is deleted |
| `tests/test_paths.py` | Remove dead path constant assertions |

### Pass unchanged

`test_subprocess_util.py`, `test_flm_server.py`, `test_notify.py`, `test_pull.py`, `test_install_smoke.py`

---

## Part 10: Rename to `ffchat`

The CLI command changes from `flowkey` to `ffchat`. The project name changes from `flowkey` to `ffchat`. The internal Python package name stays `scripts/` (no need to rename the directory).

### 10a. `pyproject.toml`

| Field | Old | New |
|---|---|---|
| `name` | `"flowkey"` | `"ffchat"` |
| `description` | `"Local-LLM-powered grammar fix, prompt rewrite, chat, and dashboard for Linux."` | `"FastFlowLM terminal chat — local LLM chat with custom slash commands, model management, and telemetry."` |
| `keywords` | `["llm", "grammar", "hotkey", "fastflowlm", "local-ai", "linux"]` | `["llm", "fastflowlm", "tui", "local-ai", "linux", "chat"]` |
| `[project.scripts]` | `flowkey = "flowkey:main"` | `ffchat = "ffchat:main"` |

### 10b. Rename `scripts/flowkey.py` → `scripts/ffchat.py`

- Rename the file: `git mv scripts/flowkey.py scripts/ffchat.py`
- The `COMMANDS` dict simplifies to one entry: `{"tui": ("tui.app", "main", "Launch the FastFlowLM TUI")}`
- The module-level logger changes from `"flowkey"` to `"ffchat"`
- The `main()` function stays the same — it's just the CLI dispatcher

### 10c. Logger names across all modules

Every module has `log = logging.getLogger("flowkey.*")`. Batch-rename all logger names from `"flowkey"` to `"ffchat"`:

- `config.py` → `"ffchat.config"`
- `engine.py` → `"ffchat.engine"`
- `flm_server.py` → `"ffchat.flm_server"`
- `llm_client.py` → `"ffchat.llm_client"`
- `telemetry.py` → `"ffchat.telemetry"`
- `pull.py` → `"ffchat.pull"`
- `benchmark.py` → `"ffchat.benchmark"`
- `notify.py` → `"ffchat.notify"`
- `paths.py` → `"ffchat.paths"`
- `subprocess_util.py` → `"ffchat.subprocess_util"`
- `version.py` → `"ffchat.version"`
- `tui/app.py` → `"ffchat.tui"`
- `tui/chat.py` → `"ffchat.tui.chat"`
- `tui/dashboard/*.py` → `"ffchat.tui.dashboard.*"`

### 10d. `scripts/version.py`

Update `APP_VERSION` source. Currently just a version string — no project name change needed here (it's just `"0.0.1"`). But if it has an `APP_NAME` constant, update it.

### 10e. `scripts/paths.py`

Update any path constants that reference `"flowkey"`:
- `APP_DIR` (likely `~/.local/share/flowkey` → `~/.local/share/ffchat`)
- `DATA_DIR`, `LOGS_DIR`, `CONFIG_FILE`, `CONFIG_SEED_FILE`
- History file paths: replace `grammar_fix_history.jsonl` with `chat_history.jsonl`
- Any marker file names

Migration: `_paths.py` should check for old `~/.local/share/flowkey/` and migrate data to `~/.local/share/ffchat/` on first run.

### 10f. `scripts/config.py`

- `DEFAULT_CHAT_MODEL` — no change (it's the model name, not project-related)
- The seed config `config.seed.json` — remove `"flowkey"` references if any

### 10g. `scripts/config.seed.json`

No rename changes needed (no project name embedded in the config values).

### 10h. `scripts/tui/app.py`

- Window title: `"Flowkey TUI"` → `"ffchat — FastFlowLM Terminal Chat"`
- Any mentions of `"flowkey"` in user-facing strings

### 10i. `scripts/tui/chat.py`

- `HELP_TEXT` — update header to say `ffchat` instead of `Flowkey`
- Any version footer references

### 10j. `flowkey.spec`

If kept (frozen binary distribution), rename:
- `name="flowkey"` → `name="ffchat"`
- Update paths accordingly

### 10k. `install.sh`

If kept as a curl-to-pip script:
- Update `APP_NAME="Flowkey"` → `APP_NAME="ffchat"`
- Update `FLOWKEY_BIN_PATH` → `FFCHAT_BIN_PATH`
- Update desktop entry Name/Comment/Exec
- Update all print messages
- Update the binary download URL to match new repo name

### 10l. Desktop entry (`install.sh` `create_tui_desktop_entry`)

```
Name=ffchat
Comment=ffchat — FastFlowLM terminal chat
Exec=ffchat tui
```

### 10m. `README.md`

- Title: `# ffchat`
- Description: `FastFlowLM Terminal Chat`
- Installation: `pip install . && ffchat tui`
- Remove all Flowkey branding references

### 10n. `CHANGELOG.md`

Add a header: `## 0.1.0 — Renamed to ffchat`

### 10o. Git and GitHub

- Rename the directory `flowkey-linux` → `ffchat` (or keep the repo name, that's a GitHub UI change)
- Update `pyproject.toml` `Homepage` and `Issues` URLs

### 10p. Conflicting binaries check

`ffchat` does not conflict with any common Linux binary:
- `ff` — not a standalone binary
- `flm` — the FastFlowLM CLI, completely different prefix
- `chat` — multiple things, but `ffchat` is a single token
- No package in Debian/Ubuntu/Fedora/Arch ships a binary called `ffchat`
