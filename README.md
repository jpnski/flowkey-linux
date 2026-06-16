# ffchat

ffchat is just a Linux TUI chat frontend for [FastFlowLM](https://github.com/FastFlowLM/FastFlowLM), direct FLM server lifecycle management, and a simple configuration dashboard.

Forked from [agr77one/Fastflow](https://github.com/agr77one/Fastflow).

## Features

- **Streaming TUI chat** - Chat with the local model from a terminal UI with configurable slash commands (grammar, summarize, explain, prompt).
- **Direct FLM server management** - TUI manages FLM server lifecycle directly without daemon processes or background services.
- **Config dashboard** - Inspect runtime status, model settings, and configuration options in a single tab.

## Requirements

### FLM Runtime

ffchat is a frontend for FastFlowLM, not a replacement for it. Install FLM first:

- FastFlowLM GitHub: <https://github.com/FastFlowLM/FastFlowLM>
- Linux getting started guide: 
<https://github.com/FastFlowLM/FastFlowLM/blob/main/docs/linux-getting-started.md>

### Linux dependencies

Base system tools such as `python3`, `git`, and a working desktop session are assumed. The non-obvious runtime dependencies are:

### Python dependencies

The release binary bundles its Python dependencies. For source or development installs, ffchat uses:

| Dependency | Purpose |
|---|---|
| `packaging` | Version parsing and comparisons |
| `pyperclip` | Clipboard access |
| `textual` | Terminal UI framework |

Development installs also use `build`, `pyinstaller`, `pytest`, and `ruff` from the `dev` extra.

## Installation

### Development install

Clone the source and run the components from the checkout:

```bash
git clone https://github.com/jpnski/ffchat-linux.git
cd ffchat-linux
python3 -m venv .venv
source .venv/bin/activate
pip install -e .[dev]
```

Run the TUI directly from the repo:

```bash
python scripts/ffchat.py
```

## Usage

The TUI provides:

- **Chat interface** - Send messages to the local FLM model with streaming responses
- **Config dashboard** - Configure model selection, FLM server settings, and other options
- **Slash commands** - Built-in commands like `/grammar`, `/summarize`, `/explain`, and `/prompt` for common text operations

## Configuration

ffchat uses a single `config.json`, but the path depends on how it is launched:

| Mode | Config location | Runtime data | Logs |
|---|---|---|---|
| Dev checkout | `./config.json` | `./data/` | `./logs/` |
| Deployed binary | `~/.local/share/ffchat/config.json` | `~/.local/share/ffchat/data/` | `~/.local/share/ffchat/logs/` |

The TUI manages the common settings most users touch often: model selection, hotkeys, performance mode, autostart, and input-processing options. The same file also stores lower-level values such as the FLM server URL, request timeout, chunking thresholds, and other defaults that are usually left alone.

## Project Structure

```text
ffchat/
├── pyproject.toml
├── README.md
├── TODO.md
├── scripts/
│   ├── ffchat.py
│   ├── engine.py
│   ├── config.py
│   ├── paths.py
│   ├── flm_server.py
│   ├── llm_client.py
│   ├── pull.py
│   └── tui/
│       ├── app.py
│       ├── chat.py
│       └── dashboard/
└── tests/
```

## License

MIT. See [LICENSE](LICENSE).
