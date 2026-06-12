"""Textual chat interface for Flowkey.

Replaces chat_popup.py with a streaming markdown chat in the terminal.

Features:
  - Streaming markdown responses from the local LLM
  - Slash-commands: /grammar, /summarize, /explain, /prompt, /tone, /clear, /help
  - Mode-prefix parsing (same as listener.py)
  - Conversation thread management via daemon
  - Non-streaming mode for grammar-fix operations
"""

from __future__ import annotations

import json
import logging
import re
import subprocess
import threading
import time
from pathlib import Path

import loopback_http
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Container, Horizontal, Vertical, VerticalScroll
from textual.events import Click
from textual.widgets import Markdown, Static, TextArea

from tui.dashboard.config_pane.flm import FlmModelPanel

log = logging.getLogger("flowkey.tui.chat")

# ---------------------------------------------------------------------------
# Constants & helpers
# ---------------------------------------------------------------------------

MODE_LABELS: dict[str, str] = {
    "grammar": "Grammar fix",
    "prompt": "Prompt rewrite",
    "summarize": "Summarize",
    "explain": "Explain",
    "tone": "Tone shift",
}

SYSTEM_PROMPTS: dict[str, str] = {
    "grammar": "Fix grammar, spelling, punctuation, capitalization, and obvious wording mistakes. Preserve meaning. Keep emoji/smiley characters exactly as written when possible. Return only corrected text.",
    "summarize": "Summarize the user text as exactly 3 bullet points. Each bullet is one sentence, factual, no preamble or sign-off. Preserve emoji/smiley characters when relevant. Return only the bullets.",
    "explain": "Explain the selected code, regex, or SQL in 2-3 plain-English sentences. Call out one non-obvious edge case if any. No preamble. Return only the explanation.",
}

HELP_TEXT = """
 ╔═════╗  ╔═════╗    ▄▄ ▄▄
 ║     ╟──╢     ║   ██  ██               ▄▄
 ╚══╤══╝  ╚══╤══╝  ▀██▀ ██ ▄███▄ ██   ██ ██ ▄█▀ ▄█▀█▄ ██ ██
 ╔══╧══╗  ╔══╧══╗   ██  ██ ██ ██ ██ █ ██ ████   ██▄█▀ ██▄██
 ║     ╟──╢     ║   ██  ██ ▀███▀  ██▀██  ██ ▀█▄ ▀█▄▄▄  ▀██▀
 ╚═════╝  ╚═════╝                                       ██
                                                      ▀▀▀

[i]Slash commands:[/i]
  /grammar <text>    — Fix grammar and wording
  /summarize <text>  — Summarize as 3 bullet points
  /explain <text>    — Explain code/regex/SQL
  /prompt <text>     — Rewrite as a Claude-ready prompt
  /tone <text>       — Shift tone (current preset)
  /clear             — Clear conversation history
  /help              — Show this help

[i]Mode prefixes:[/i]
  grammar: <text>    — Same as /grammar
  summarize: <text>  — Same as /summarize
  explain: <text>    — Same as /explain

[i]Shortcuts:[/i]
  F1                 — Chat
  F2                 — Dashboard
  Ctrl+P             — Commands
  Ctrl+C             — Quit (press twice)
"""


class ChatHistory:
    """Thread-safe message history with context-window trimming."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._messages: list[dict] = []
        self._seq: int = 0

    @property
    def last(self) -> dict | None:
        return self._messages[-1] if self._messages else None

    @property
    def messages(self) -> list[dict]:
        return self._messages

    def __len__(self) -> int:
        return len(self._messages)

    def add(self, role: str, content: str) -> dict:
        with self._lock:
            self._seq += 1
            msg = {
                "role": role,
                "content": content,
                "timestamp": time.time(),
                "msg_id": self._seq,
            }
            self._messages.append(msg)
            return msg

    def trim_to_window(self, context_window_turns: int, extra: int = 0) -> None:
        if context_window_turns > 0:
            max_msgs = context_window_turns * 2 + extra
            if len(self._messages) > max_msgs:
                with self._lock:
                    self._messages[:] = self._messages[-max_msgs:]

    def update_last_assistant(self, content: str) -> None:
        if self._messages and self._messages[-1]["role"] == "assistant":
            self._messages[-1]["content"] = content

    def to_payload(self) -> list[dict]:
        return [{"role": m["role"], "content": m["content"]} for m in self._messages]

    def clear(self) -> None:
        with self._lock:
            self._messages.clear()
            self._seq = 0


# ---------------------------------------------------------------------------
# Message bubble widget
# ---------------------------------------------------------------------------


class MessageBubble(Horizontal):
    """A single chat message with role-based styling and a copy button on assistant responses."""

    def __init__(self, role: str, content: str, is_streaming: bool = False, *,
                 show_role: bool = True, msg_id: int = 0) -> None:
        super().__init__()
        self._role = role
        self._content = content
        self._is_streaming = is_streaming
        self._show_role = show_role
        self._msg_id = msg_id

    def compose(self) -> ComposeResult:
        content = self._content
        if self._role == "user":
            content = content.replace("[", "[[")

        # Assistant messages render as Markdown (bold/italic/code/lists work).
        # User messages and help-text render as plain Static.
        if self._role == "assistant" and self._show_role:
            yield Markdown(content, classes="message-content", id=f"content-{self._msg_id}")
        else:
            yield Static(content, classes="message-content", id=f"content-{self._msg_id}")

        # Assistant bubbles get a copy button; help-text (show_role=False) does not.
        if self._role == "assistant" and self._show_role:
            yield Static("📋", id=f"copy-{self._msg_id}", classes="copy-btn")

    # -- helpers ----------------------------------------------------------------

    @staticmethod
    def _normalize_for_markdown(text: str) -> str:
        """Strip Textual markup tags (e.g. [red], [/red]) for clean Markdown.

        These tags appear in error/status messages produced by ChatWidget;
        they are not valid Markdown and would render as literal text.
        """
        return re.sub(r'\[/?(?:red|yellow|green|blue|bold|italic|dim|strike|underline)\]', '', text)

    def _rendered_content(self) -> str:
        """Return content with user-markup escaping applied."""
        c = self._content
        if self._role == "user":
            c = c.replace("[", "[[")
        return c

    # -- public API used by ChatWidget -----------------------------------------

    def update_content(self, content: str) -> None:
        """Update content in-place (for streaming).

        Assistant bubbles (Markdown widget) get Textual-markup stripped for
        clean display.  User/help bubbles (Static widget) get bracket escaping.
        """
        self._content = content
        try:
            widget = self.query_one(f"#content-{self._msg_id}")
            if isinstance(widget, Markdown):
                widget.update(self._normalize_for_markdown(content))
            elif isinstance(widget, Static):
                widget.update(self._rendered_content())
        except Exception as exc:
            log.warning("could not update bubble content: %s", exc)

    def finalize_stream(self) -> None:
        """Mark streaming as complete — no role label to update anymore."""
        self._is_streaming = False

    # -- event handlers --------------------------------------------------------

    def on_click(self, event: Click) -> None:
        if event.widget.id == f"copy-{self._msg_id}":
            import pyperclip
            try:
                pyperclip.copy(self._content)
                self.app.notify("Copied", timeout=1.5)
            except Exception as exc:
                log.warning("copy failed: %s", exc)
                self.app.notify("Copy failed", severity="error", timeout=2)


# ---------------------------------------------------------------------------
# Chat main widget
# ---------------------------------------------------------------------------


class ChatWidget(Container):
    """Main chat container: message log + input bar."""

    DEFAULT_CSS = """
    ChatWidget {
        layout: vertical;
        height: 100%;
    }

    #chat-messages {
        height: 1fr;
        overflow-y: auto;
        padding: 0 1;
    }

    #chat-messages > MessageBubble {
        height: auto;
        margin: 0 0 1 0;
        padding: 1;
        min-height: 3;
    }

    #chat-messages > MessageBubble.user {
        background: $surface;
        border: solid $secondary;
        border-left: thick $secondary;
    }

    #chat-messages > MessageBubble.assistant {
        background: $surface;
        border: solid $primary;
        border-left: thick $primary;
    }

    #chat-input-row {
        height: auto;
        padding: 0 1 1 1;
        background: $surface;
    }

    #chat-input {
        width: 1fr;
        height: auto;
        min-height: 3;
        max-height: 12;
    }

    #connection-status {
        width: 1fr;
        height: 1;
        color: $text-muted;
        margin-top: 1;
    }

    .message-content {
        width: 1fr;
    }

    /* Text color: user messages faded, assistant messages vivid */
    #chat-messages > MessageBubble.user .message-content {
        color: $text-muted;
    }
    #chat-messages > MessageBubble.assistant .message-content {
        color: $text;
    }



    /* Copy button on assistant response bubbles (clickable Static) */
    .copy-btn {
        width: 3;
        height: 1;
        padding: 0;
        text-align: center;
        color: $text-muted;
    }
    .copy-btn:hover {
        color: $text;
        background: $surface;
    }
    """

    BINDINGS = [
        Binding("enter", "submit_chat", "Submit", priority=True),
    ]

    def __init__(self) -> None:
        super().__init__()
        self._history = ChatHistory()
        self._pending_multi_line: str | None = None
        self._suppress_change: bool = False
        self._prev_text: str = ""
        self._streaming_active = False
        self._current_bubble: MessageBubble | None = None
        self._llm_base_url = "http://127.0.0.1:52625"
        self._llm_model = "gemma4-it:e4b"
        self._daemon_available = False
        # Timestamp of the last set_model() call.  _refresh_config uses this
        # to avoid overwriting _llm_model with stale data from the daemon
        # before its config_snapshot has converged after a model change.
        self._model_set_at: float = 0.0
        # Chat config values — driven by config.json chat section,
        # populated by _refresh_config on each daemon snapshot poll.
        self._temperature: float = 0.3
        self._max_tokens: int = 1024
        self._system_prompt: str = "You are a concise, helpful local assistant."
        self._request_timeout: int = 240
        self._context_window_turns: int = 12
        # Cached reference to the footer Static — set in on_mount.
        # Avoids query_one() calls from async worker contexts (which can
        # silently fail when the Chat tab is not the active tab).
        self._status_widget: Static | None = None

    def is_streaming(self) -> bool:
        """True while an LLM stream is in flight (chat or mode-fix)."""
        return self._streaming_active

    def compose(self) -> ComposeResult:
        with VerticalScroll(id="chat-messages"):
            yield MessageBubble("assistant", HELP_TEXT, show_role=False)

        with Vertical(id="chat-input-row"):
            yield TextArea(
                placeholder="Type a message... (or /grammar, /help, ...)",
                id="chat-input",
                soft_wrap=True,
            )
            yield Static("", id="connection-status")

    def on_mount(self) -> None:
        self._status_widget = self.query_one("#connection-status", Static)
        self._status_widget.update("Connecting to daemon…")
        self._refresh_config()
        self.set_interval(5.0, self._refresh_config)

    # ---- Config refresh ----

    def _refresh_config(self) -> None:
        """Pull live config from daemon."""
        try:
            resp = loopback_http.daemon_post("config_snapshot", timeout=2.0)
            if resp.get("ok") and isinstance(resp.get("result"), dict):
                result = resp["result"]
                self._llm_base_url = str(result.get("flm_api", {}).get("url") or self._llm_base_url)

                daemon_model = str(result.get("flm_server", {}).get("model") or "")
                # True when the FLM server is reachable (TCP port open). The
                # config patch flow additionally runs a warmup request before
                # returning, so by the time `flm_model_loaded` goes True the
                # model has responded to a real API call — not just opened a
                # port.
                model_loaded = bool(result.get("flm_server", {}).get("flm_model_loaded", False))

                # Has set_model() been called recently?  If so we trust the
                # explicitly pushed model (it came from a daemon-confirmed
                # model-switch) and skip the daemon-snapshot value.
                recent_push = time.monotonic() - self._model_set_at <= 60.0

                if daemon_model:
                    # Accept the daemon's model name only when it hasn't been
                    # overwritten by set_model() in the last 60 seconds.
                    if not recent_push:
                        self._llm_model = daemon_model
                    # Show the model name only when the model is confirmed
                    # loaded, OR when it was just explicitly pushed (which
                    # happens after a daemon-confirmed model switch).
                    if model_loaded or recent_push:
                        model_display = self._llm_model
                    else:
                        model_display = "(none)"
                else:
                    # Daemon has no model configured (e.g. FLM not running yet).
                    self._llm_model = ""
                    model_display = "(none)"

                # Read chat config (temperature, max_tokens, system_prompt,
                # request timeout) from the daemon snapshot.
                chat_cfg = result.get("chat") or {}
                self._temperature = float(chat_cfg.get("temperature") or self._temperature)
                self._max_tokens = int(chat_cfg.get("max_tokens") or self._max_tokens)
                sp = str(chat_cfg.get("system_prompt") or "").strip()
                if sp:
                    self._system_prompt = sp
                self._request_timeout = int(chat_cfg.get("request_timeout_s") or self._request_timeout)
                self._context_window_turns = int(chat_cfg.get("context_window_turns") or self._context_window_turns)

                self._daemon_available = True
                self._update_status(f"Model: {model_display}  |  Daemon: connected")
                return
        except Exception as exc:
            log.warning("daemon config poll failed: %s", exc)
        self._daemon_available = False
        self._update_status("Daemon: not connected — config may be stale")

    def set_model(self, model_name: str) -> None:
        """Directly push the active model name (bypasses daemon poll).

        Called by FlmModelPanel after a successful model change so the chat
        footer and request target are correct *before* the daemon's config
        snapshot converges.  Avoids the race where _refresh_config polls the
        daemon and gets stale data.
        """
        self._llm_model = model_name
        self._model_set_at = time.monotonic()
        self._daemon_available = True
        self._update_status(
            f"Model: {model_name if model_name else '(none)'}  |  Daemon: connected"
        )

    def _update_status(self, text: str) -> None:
        if self._status_widget is not None:
            self._status_widget.update(text)

    # ---- Input handling ----

    def action_submit_chat(self) -> None:
        inp = self.query_one("#chat-input", TextArea)
        if not inp.has_focus:
            return
        text = self._pending_multi_line or inp.text
        if text.strip():
            self._process_input(text)
        inp.clear()
        self._pending_multi_line = None

    def on_text_area_changed(self, event: TextArea.Changed) -> None:
        if self._suppress_change:
            return
        if event.text_area.id == "chat-input":
            text = event.text_area.text
            if not text:
                self._prev_text = ""
                self._pending_multi_line = None
                return
            line_count = text.count("\n")
            if line_count > 0:
                self._pending_multi_line = text
                prefix = self._prev_text
                self._prev_text = f"{prefix} [{line_count} pasted lines]" if prefix else f"[{line_count} pasted lines]"
                self._suppress_change = True
                event.text_area.text = self._prev_text
                self._suppress_change = False
            else:
                self._prev_text = text

    # ---- Message processing ----

    def _process_input(self, text: str) -> None:
        text = text.strip()
        if not text:
            return
        if self._streaming_active:
            return

        # Block input while the FLM model is being restarted — the XRT
        # NPU context would be destroyed mid-inference, causing an error.
        try:
            panel = self.app.query_one(FlmModelPanel)
            if panel.restarting:
                self.app.notify(
                    "Model is restarting — please wait before chatting",
                    severity="warning", timeout=4,
                )
                return
        except Exception as exc:
            log.warning("could not check model restart status: %s", exc)

        # Check for slash commands
        if text.startswith("/"):
            self._handle_slash_command(text)
            return

        # Check for mode prefix
        mode, body = self._parse_mode_and_text(text)
        if mode != "grammar" and body:
            self._handle_mode_fix(mode, body)
            return

        # Default: regular chat message
        self._send_chat_message(text)

    def _handle_slash_command(self, text: str) -> None:
        parts = text.split(maxsplit=1)
        cmd = parts[0].lower()
        args = parts[1] if len(parts) > 1 else ""

        if cmd == "/clear":
            self._clear_history()
        elif cmd == "/help":
            self._add_message("assistant", HELP_TEXT, show_role=False)
        elif cmd in ("/grammar",):
            self._handle_mode_fix("grammar", args or self._get_last_selection())
        elif cmd in ("/summarize",):
            self._handle_mode_fix("summarize", args or self._get_last_selection())
        elif cmd in ("/explain",):
            self._handle_mode_fix("explain", args or self._get_last_selection())
        elif cmd in ("/prompt",):
            self._handle_mode_fix("prompt", args)
        elif cmd in ("/tone",):
            self._handle_mode_fix("tone", args)
        else:
            self._add_message("assistant", f"Unknown command: {cmd}\n\nType /help for available commands.")

    def _handle_mode_fix(self, mode: str, text: str) -> None:
        """Run a grammar-fix subprocess for the given mode."""
        if not text:
            self._add_message("assistant", f"[yellow]No text provided for {mode} mode.[/]")
            return

        self._add_message("user", f"[{MODE_LABELS.get(mode, mode)}] {text[:200]}{'…' if len(text) > 200 else ''}")
        self._add_message("assistant", f"Running {mode}...", is_streaming=True)

        def _run():
            try:
                result = self._run_grammar_fix(mode, text)
                self.call_later(self._finalize_stream, result)
            except Exception as exc:
                self.call_later(self._finalize_stream, f"[red]Error: {exc}[/]")

        threading.Thread(target=_run, daemon=True).start()

    def _run_grammar_fix(self, mode: str, text: str) -> str:
        """Run flowkey-grammar-fix subprocess."""
        import tempfile

        infile = None
        outfile = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w", suffix=".txt", prefix="flowkey_in_",
                delete=False, encoding="utf-8",
            ) as f_in:
                f_in.write(text)
                infile = f_in.name

            outfile_obj = tempfile.NamedTemporaryFile(
                mode="w", suffix=".txt", prefix="flowkey_out_",
                delete=False, encoding="utf-8",
            )
            outfile = outfile_obj.name
            outfile_obj.close()

            result = subprocess.run(
                ["flowkey-grammar-fix", "--mode", mode, "--input-file", infile, "--output-file", outfile],
                capture_output=True, text=True, timeout=120, check=False,
            )
            if result.returncode != 0:
                err = (result.stderr or "").strip() or f"exit code {result.returncode}"
                return f"[red]Grammar fix failed: {err}[/]"

            output = Path(outfile).read_text(encoding="utf-8").strip()
            return output or "[yellow]No output returned[/]"

        except subprocess.TimeoutExpired:
            return "[red]Grammar fix timed out after 120s[/]"
        except OSError as exc:
            return f"[red]Failed to launch grammar fix: {exc}[/]"
        finally:
            for p in (infile, outfile):
                if p:
                    try:
                        Path(p).unlink()
                    except OSError:
                        pass

    def post_ingested_text(self, text: str) -> None:
        """Called by the TUI app when launched with --ingest-file.

        Sends the text into the chat as if the user typed it.
        """
        if text:
            self._send_chat_message(text)

    def _send_chat_message(self, text: str) -> None:
        """Send a regular chat message to the LLM with streaming."""
        # Guard: don't send if FLM is restarting.
        try:
            panel = self.app.query_one(FlmModelPanel)
            if panel.restarting:
                self.app.notify(
                    "Model is restarting — try again shortly",
                    severity="warning", timeout=4,
                )
                return
        except Exception as exc:
            log.warning("model restart check failed: %s", exc)
        self._add_message("user", text)
        self._add_message("assistant", "…", is_streaming=True)

        # Trim history to the last N complete exchanges (context_window_turns).
        # Each exchange is a user↔assistant pair (2 messages). Add 2 for the
        # current in-progress exchange (<user_msg>, "…") that was just added.
        self._history.trim_to_window(self._context_window_turns, extra=2)

        history = self._history.to_payload()

        threading.Thread(
            target=self._stream_llm_response,
            args=(text, list(history)),
            daemon=True,
        ).start()

    def _stream_llm_response(self, user_text: str, history: list[dict]) -> None:
        """Stream LLM response via OpenAI-compatible SSE endpoint."""
        # Ensure the FLM server is running before attempting the chat request.
        try:
            resp = loopback_http.daemon_post("start", timeout=30.0)
            if not resp.get("ok"):
                raise RuntimeError(str(resp.get("error") or "start failed"))
            # Server started — schedule an immediate config refresh so the
            # footer flips from "(none)" to the model name without waiting
            # for the next poll interval (up to 30s with the old timer).
            self.call_later(self._refresh_config)
        except Exception as exc:
            self.call_later(self._finalize_stream,
                            f"[red]Could not start LLM server: {exc}[/]")
            return

        import urllib.request

        messages = [{"role": "system", "content": self._system_prompt}]
        messages.extend(history)

        body = json.dumps({
            "model": self._llm_model,
            "messages": messages,
            "stream": True,
            "temperature": self._temperature,
            "max_tokens": self._max_tokens,
        }).encode("utf-8")

        req = urllib.request.Request(
            f"{self._llm_base_url}/v1/chat/completions",
            data=body,
            headers={
                "Content-Type": "application/json",
                "Authorization": "Bearer flm",
            },
            method="POST",
        )

        full_content = ""
        try:
            with urllib.request.urlopen(req, timeout=self._request_timeout) as resp:
                buffer = ""
                while True:
                    chunk = resp.read(4096)
                    if not chunk:
                        break
                    buffer += chunk.decode("utf-8", errors="replace")
                    # Parse SSE events
                    while "\n" in buffer:
                        line, buffer = buffer.split("\n", 1)
                        line = line.strip()
                        if line.startswith("data: "):
                            data_str = line[6:]
                            if data_str == "[DONE]":
                                break
                            try:
                                data = json.loads(data_str)
                                delta = (
                                    data.get("choices", [{}])[0]
                                    .get("delta", {})
                                    .get("content", "")
                                )
                                if delta:
                                    full_content += delta
                                    self.call_later(
                                        self._update_stream, full_content
                                    )
                            except json.JSONDecodeError:
                                continue

            self.call_later(self._finalize_stream, full_content or "[red]No response from LLM[/]")

        except urllib.error.HTTPError as exc:
            error_body = ""
            try:
                error_body = exc.read().decode("utf-8", errors="replace")[:200]
            except Exception as inner:
                log.warning("could not decode error body: %s", inner)
            err_msg = f"[red]LLM HTTP {exc.code}: {error_body}[/]"
            self.call_later(self._finalize_stream, err_msg)
        except Exception as exc:
            err_msg = f"[red]LLM error: {exc}[/]"
            self.call_later(self._finalize_stream, err_msg)

    # ---- Message management ----

    def _add_message(self, role: str, content: str, is_streaming: bool = False,
                     show_role: bool = True) -> None:
        """Add a message to the chat log."""
        msg = self._history.add(role, content)
        msg_id = msg["msg_id"]

        messages = self.query_one("#chat-messages", VerticalScroll)
        bubble = MessageBubble(role, content, is_streaming=is_streaming,
                               show_role=show_role, msg_id=msg_id)
        bubble.add_class(role)
        messages.mount(bubble)
        messages.scroll_end(animate=False)

        if not is_streaming:
            self._current_bubble = None
        else:
            self._current_bubble = bubble
            self._streaming_active = True

    def _update_stream(self, content: str) -> None:
        """Update the current streaming bubble with new content."""
        if self._current_bubble is not None:
            self._current_bubble.update_content(content)

    def _finalize_stream(self, content: str) -> None:
        """Finalize a streaming or processing response."""
        if self._current_bubble is not None:
            self._current_bubble.update_content(content)
            self._current_bubble.finalize_stream()
            self._current_bubble = None

        self._streaming_active = False

        # Update history with final content
        self._history.update_last_assistant(content)

        messages = self.query_one("#chat-messages", VerticalScroll)
        messages.scroll_end(animate=True)

    def _clear_history(self) -> None:
        """Clear all messages."""
        self._history.clear()
        messages = self.query_one("#chat-messages", VerticalScroll)
        messages.remove_children()
        self._add_message("assistant", "— conversation cleared —")

    def _get_last_selection(self) -> str:
        """Placeholder: get last clipboard content as fallback."""
        import pyperclip
        try:
            return pyperclip.paste()
        except Exception as exc:
            log.debug("clipboard paste failed: %s", exc)
            return ""

    # ---- Mode prefix parsing (ported from listener.py) ----

    _MODE_PREFIX_ENTRIES: list[tuple[str, str]] = [
        ("prompt", r"(?:prompts|prompt)"),
        ("summarize", r"(?:summarizes|summarize)"),
        ("explain", r"(?:explains|explain)"),
        ("tone", r"tone"),
    ]

    def _parse_mode_and_text(self, text: str) -> tuple[str, str]:
        """Detect mode prefix keywords on the first non-empty line.

        Returns (mode, body_text) where mode is one of 'grammar', 'prompt',
        'summarize', 'explain', 'tone'. Default mode is 'grammar'.
        """
        raw = text.strip("\r\n\t ")
        if raw and raw[0] == "\ufeff":
            raw = raw[1:]
        if not raw:
            return ("grammar", "")
        lines = raw.split("\n")
        first_idx: int | None = None
        for i, line in enumerate(lines):
            stripped = line.strip("\t ")
            if stripped:
                first_idx = i
                break
        if first_idx is not None:
            first_line = lines[first_idx].strip("\t ")
            for mode, kw in self._MODE_PREFIX_ENTRIES:
                pattern = r"^\s*[>\-\*]*\s*/?" + kw + r"(\s*:\s*|\s*-\s+|$|\s+)(.*)$"
                m = re.match(pattern, first_line, re.IGNORECASE)
                if m:
                    parts: list[str] = []
                    inline_body = m.group(2).strip("\t ")
                    if inline_body:
                        parts.append(inline_body)
                    for j in range(first_idx + 1, len(lines)):
                        stripped_line = lines[j].strip("\t ")
                        if stripped_line:
                            parts.append(stripped_line)
                    body = "\n".join(parts).strip("\r\n\t ")
                    return (mode, body)
        # Inline check
        for mode, kw in self._MODE_PREFIX_ENTRIES:
            pattern = r"^\s*[>\-\*]*\s*/?" + kw + r"(\s*:\s*|\s*-\s+|\s+)(.+)$"
            m = re.match(pattern, raw, re.IGNORECASE)
            if m:
                body = m.group(2).strip("\r\n\t ")
                return (mode, body)
        return ("grammar", raw)
