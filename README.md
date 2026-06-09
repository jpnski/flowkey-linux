# Flowkey — Linux Desktop Assistant for FastFlowLM

A Linux-native desktop assistant for [FastFlowLM](https://github.com/FastFlowLM/FastFlowLM) (FLM), a local LLM server. Flowkey provides global hotkeys, clipboard integration, a terminal TUI, and a system tray indicator — all running locally, no cloud dependency.

Forked from [agr77one/Fastflow](https://github.com/agr77one/Fastflow). This is a pure Linux port — all Windows dependencies (AutoHotkey, PowerShell, WinAPI, Registry) removed, replaced with native Linux equivalents.

---

## Features

- **Global hotkeys** — Select text anywhere, press a key combo, get grammar/prompt/summarize/explain/tone results pasted back in-place (X11: `pynput`, Wayland: `evdev`)
- **Clipboard watcher** — Optional live monitoring: detects URLs, code, or stack traces on copy and suggests the right mode
- **Markdown TUI chat** — Streaming LLM responses, slash commands, conversation history
- **Multi-pane TUI dashboard** — Config (model, hotkeys, server, chunking), telemetry, benchmark, notes vault, and history — each pane built from interactive sub-panels
- **System tray** — Quick server start/stop, warmup, performance mode toggle
- **Note capture** — Save selections to categorized notes (Ctrl+Alt+N)
- **Context-aware modes** — Grammar fix, prompt rewriting (Claude-ready), summarization, code/regex/SQL explanation, tone shifting, ask selection in chat
- **Clipboard routing** — Mode prefixes (`grammar:`, `summarize:`, `explain:`, `prompt:`, `tone:`) inline in selected text

---

## Requirements

- **OS:** Linux (X11 or Wayland)
- **Python:** 3.11+
- **LLM Server:** [FastFlowLM](https://github.com/FastFlowLM/FastFlowLM) (`flm` CLI) with a model (default: `gemma4-it:e4b`)

#### System packages (installed automatically by `install.sh`)

| Package | Purpose |
|---|---|
| `xdotool` | X11 window info + key simulation |
| `ydotool` | Wayland key simulation (paste-back) |
| `wl-clipboard` | Wayland clipboard access |
| `libnotify-bin` / `libnotify` | Desktop notifications (`notify-send`) |

#### Python dependencies (installed via pip)

| Dependency | Purpose |
|---|---|
| `pyperclip` | Cross-platform clipboard access |
| `textual>=8.0` | Terminal UI framework |
| `pynput>=1.7` | X11 global hotkeys (X11 only) |
| `evdev>=1.7` | Wayland hotkey capture (Wayland only) |
| `dasbus>=1.7` | Wayland StatusNotifier tray icon (Wayland only) |
| `pystray>=0.19` | System tray icon (X11 only) |
| `trafilatura>=1.8` | Webpage readability extraction (optional) |

---

## Installation

#### Option 1: Automated install (recommended)

```bash
# System-wide (requires sudo for system packages)
./install.sh

# User-local (no sudo needed)
./install.sh --user

# Into a virtual environment
./install.sh --venv /path/to/venv
```

The script handles: system packages, `input` group membership, udev rules for evdev, `pip install`, config bootstrap, XDG desktop entries, and model pull.

**Note:** System packages, group setup, and udev rules require `sudo` and are only applied in the default (system-wide) mode. In `--user` or `--venv` mode these steps are skipped — install system deps manually if needed.

#### Option 2: Manual pip install

```bash
# Install core
pip install .

# With X11 hotkeys + tray
pip install .[x11,tray]

# With Wayland hotkeys + tray
pip install .[wayland,tray]

# With all extras
pip install .[x11,wayland,tray,readability]

# For development
pip install -e .[dev]
```

Then run `flowkey-install` to set up config, autostart, and pull the model.

#### Option 3: Development from source

```bash
git clone https://github.com/jpnski/flowkey-linux.git
cd flowkey-linux
python3 -m venv venv
source venv/bin/activate
pip install -e .[dev]

# Install system deps manually:
#   sudo apt install xdotool ydotool wl-clipboard libnotify-bin
```

---

## Quick Start

```bash
# 1. Start the action daemon (background HTTP server, port 52650)
flowkey-daemon

# 2. Start the global hotkey listener
flowkey-listener

# 3. Or just use the TUI directly (auto-starts daemon)
flowkey-tui

# CLI grammar/prompt processing (standalone, no daemon needed)
flowkey-grammar-fix --text "grammar: i seen him yesterday"
```

**First-run:** `flowkey-install` will pull the default model (`gemma4-it:e4b`) if not already installed:

```bash
flowkey-install
```

**Autostart:** The installer creates an XDG autostart entry for `flowkey-listener` at `~/.config/autostart/flowkey-listener.desktop`. You can toggle autostart from the tray or dashboard.

**Lifecycle:** All daemons accept `--parent-pid <pid>` to exit automatically when the parent process dies — useful when launched from the TUI or tray.

---

## Configuration

Config file: `~/.local/share/Flowkey/config.json` (or `$XDG_DATA_HOME/Flowkey/config.json`)

Key settings:

| Key | Default | Description |
|---|---|---|
| `theme` | `textual-dark` | TUI theme name (Textual built-in, e.g. `dracula`, `catppuccin-mocha`) |
| `flm_base_url` | `http://127.0.0.1:52625` | FLM server address |
| `flm_model` | `gemma4-it:e4b` | LLM model |
| `flm_timeout_seconds` | 60 | Request timeout |
| `server.performance_mode` | `balanced` | `balanced` or `max` |
| `hotkeys.grammar_fix` | `ctrl+alt+g` | Ctrl+Alt+G |
| `hotkeys.open_chat` | `ctrl+alt+t` | Ctrl+Alt+T |
| `hotkeys.capture_note` | `ctrl+alt+n` | Ctrl+Alt+N |
| `hotkeys.ask_chat` | `ctrl+alt+a` | Ctrl+Alt+A |

### Hotkey notation

Hotkeys are stored as human-readable modifier+letter strings (`ctrl+alt+g`). Modifier keys available: `ctrl`, `super` (Win key), `alt`. Example: `ctrl+alt+g` → Ctrl+Alt+G.

### Mode prefixes

Prefix the first line of your selected text with a mode directive:
- `grammar:` — Fix grammar and wording
- `summarize:` — 3-bullet summary
- `explain:` — Explain code/regex/SQL
- `prompt:` — Rewrite as Claude-ready prompt
- `tone:` — Apply tone preset (formal/casual/friendly)
- `ask:` — Send selected text to the chat tab for further conversation

Example: Select `summarize: Quarterly report shows 12% growth in Q3...` and press your hotkey → gets replaced with a 3-bullet summary.

---

## TUI Usage Guide

Launch: `flowkey-tui`

### Chat tab (F1)

- **Type a message** and press Enter to send (streaming response from LLM)
- **Slash commands:**
  - `/grammar <text>` — Fix grammar and wording
  - `/summarize <text>` — Summarize as 3 bullet points
  - `/explain <text>` — Explain code/regex/SQL
  - `/prompt <text>` — Rewrite as a Claude-ready prompt
  - `/tone <text>` — Shift tone (current preset)
  - `/ask <text>` — Send selected text to chat
  - `/clear` — Clear conversation history
  - `/help` — Show help
- **Mode prefixes** work inline (same as hotkey mode)

### Dashboard tab (F2)

Five tabbed panes, auto-refreshing every 10s. Each pane is composed of interactive sub-panels:

| Pane | Sub-panels |
|---|---|
| **Config** | FLM Runtime (version + update check), FLM Model (select/pull models), Chat Settings (tone, performance, input processing, auto-start), Input Processing (chunking thresholds), Hotkeys (modifier + letter bindings) |
| **Telemetry** | Counters by mode, latency percentiles, token counts, tok/s |
| **Benchmark** | Run benchmarks, recent results table |
| **Notes** | Vault directory, categories, note count |
| **History** | Recent 50 entries from grammar fix history |

### Keyboard shortcuts

| Key | Action |
|---|---|
| `F1` | Switch to Chat tab |
| `F2` | Switch to Dashboard tab |
| `Ctrl+P` | Open command palette (theme browser, search, etc.) |
| `Ctrl+C` | Quit (press twice) |

---

## System Tray

Launch: `flowkey-tray`

Uses `pystray` on X11, `dasbus StatusNotifierItem` on Wayland (falls back gracefully).

Menu:
- **Open TUI** — Launches `flowkey-tui`
- **Server** → Status / Start / Stop / Warmup
- **Performance** → Balanced / Max
- **Exit** — Quits the tray icon

---

## Troubleshooting

### Hotkeys don't work on Wayland

1. Ensure you're in the `input` group: `groups $USER | grep input`
2. Verify udev rule: `cat /etc/udev/rules.d/99-flowkey-listener.rules`
3. Re-log or reboot after group changes
4. Check that `ydotool` is installed for paste-back

### Hotkeys don't work on X11

1. Verify `pynput` is installed: `pip list | grep pynput`
2. Try running `flowkey-listener` from a terminal to see debug output

### "Connection refused" when launching TUI

The daemon auto-starts on demand. If it doesn't, start it manually:

```bash
flowkey-daemon
```

### notify-send not working

Install `libnotify-bin` (Debian/Ubuntu) or `libnotify` (Fedora/Arch).
The listener falls back to stderr output if notifications are unavailable.

### Model not found

Pull the default model or specify your own:

```bash
flm pull gemma4-it:e4b    # default
flowkey-install --model <name>  # custom model
```

### Paste-back doesn't work on Wayland

Install `ydotool`. If unavailable, the listener copies the result to your clipboard and shows a notification — press Ctrl+V manually.

### Clipboard watcher not detecting content

Enable the watcher:

```bash
touch ~/.local/share/Flowkey/data/.clipboard_watcher_on
```

The watcher polls every 1s and suggests modes for detected URLs, code, or stack traces.

---

## Development

```bash
git clone <repo>
cd flowkey-linux
python3 -m venv venv
source venv/bin/activate
pip install -e .[dev]

# Run tests
python -m pytest tests -q

# Lint
ruff check scripts tests
```

### Project structure

```
flowkey-linux/
├── CHANGELOG.md                 # Release history
├── install.sh                   # Linux system installer
├── pyproject.toml               # Package config + dependencies
├── scripts/                     # Python source
│   ├── daemon.py                # HTTP action daemon (port 52650)
│   ├── listener.py              # Global hotkey listener
│   ├── tray.py                  # System tray indicator
│   ├── tui/
│   │   ├── app.py               # TUI entry point + main screen
│   │   ├── chat.py              # Streaming chat widget
│   │   └── dashboard/           # Dashboard (package)
│   │       ├── __init__.py      # DashboardWidget, shared CSS
│   │       ├── _daemon.py       # Daemon HTTP helpers
│   │       ├── _pane.py         # Pane base class
│   │       ├── config_pane/     # Config pane (5 interactive sub-panels)
│   │       │   ├── __init__.py
│   │       │   ├── chat_settings.py
│   │       │   ├── flm_panel.py
│   │       │   ├── flm_runtime.py
│   │       │   ├── hotkeys.py
│   │       │   └── input_processing.py
│   │       ├── telemetry.py
│   │       ├── benchmark.py
│   │       ├── notes.py
│   │       └── history.py
│   ├── grammar_fix.py           # CLI grammar/prompt processing
│   ├── flm_server.py            # FLM server lifecycle helpers
│   ├── llm_client.py            # OpenAI-compatible LLM client
│   ├── config.py                # Config loading + defaults
│   ├── paths.py                 # Cross-platform path resolution
│   ├── install.py               # Python-level installer
│   ├── telemetry.py             # Performance counters + history
│   ├── actions.py               # Shared action definitions
│   ├── loopback_http.py         # HTTP helpers for daemon IPC
│   ├── notes.py                 # Note vault management
│   ├── notify.py                # Desktop notifications
│   ├── benchmark.py             # LLM benchmark runner
│   ├── pull.py                  # Model pull manager
│   ├── subprocess_util.py       # Subprocess helpers
│   ├── tools.py                 # Shared utility functions
│   ├── updater.py               # Self-update for flowkey
│   ├── _data/
│   │   └── config.json          # Seed config (shipped with package)
│   └── assets/
│       ├── flowkey.png          # Tray icon (256x256)
│       └── flowkey.ico          # Windows icon (fallback)
├── tests/                       # pytest test suite
└── TODO.md                      # Porting plan
```

### Architecture

```
┌───────────────┐      HTTP      ┌───────────────┐
│  flowkey-tui  │ ◄────JSON────► │               │
│  (Textual)    │                │  flowkey-     │
├───────────────┤                │  daemon       │
│  flowkey-tray │ ◄────JSON────► │  (port 52650) │
│  (pystray)    │                │               │
├───────────────┤                │  ┌─────────┐  │
│  flowkey-     │ ◄────POST────► │  │grammar_ │  │
│  listener     │   /action/*    │  │fix      │  │
│  (pynput/     │                │  └─────────┘  │
│   evdev)      │                │  ┌─────────┐  │
└───────────────┘                │  │llm_     │  │
                                 │  │client   │──┼──► FastFlowLM (:52625)
                                 │  └─────────┘  │
                                 └───────────────┘
```

---

## License

MIT — see [LICENSE](LICENSE).

## Original project

Forked from [agr77one/Fastflow](https://github.com/agr77one/Fastflow).
