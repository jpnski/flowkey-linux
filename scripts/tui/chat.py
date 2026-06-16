"""Textual chat interface for ffchat.

Features:
  - Streaming markdown responses from the local LLM
  - Config-driven slash commands (/grammar, /summarize, /explain, /prompt, ...)
  - Conversation thread management
  - Direct FLM server lifecycle (no daemon)
"""

from __future__ import annotations

import json
import logging
import re
import threading
import time

import engine
import version
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Container, Horizontal, Vertical, VerticalScroll
from textual.events import Click
from textual.widgets import Markdown, Static, TextArea

import config as _config

from tui.dashboard.config_pane.flm import FlmModelPanel

log = logging.getLogger("ffchat.tui.chat")

DEFAULT_SYSTEM_PROMPT = "You are a concise, helpful local assistant."


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

    def to_payload(self, skip_system: bool = True) -> list[dict]:
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

        if self._role == "assistant" and self._show_role:
            yield Markdown(content, classes="message-content", id=f"content-{self._msg_id}")
        else:
            yield Static(content, classes="message-content", id=f"content-{self._msg_id}")

        if self._role == "assistant" and self._show_role:
            yield Static("📋", id=f"copy-{self._msg_id}", classes="copy-btn")

    # -- helpers ----------------------------------------------------------------

    @staticmethod
    def _normalize_for_markdown(text: str) -> str:
        return re.sub(r'\[/?(?:red|yellow|green|blue|bold|italic|dim|strike|underline)\]', '', text)

    def _rendered_content(self) -> str:
        c = self._content
        if self._role == "user":
            c = c.replace("[", "[[")
        return c

    # -- public API used by ChatWidget -----------------------------------------

    def update_content(self, content: str) -> None:
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

    #chat-footer-row {
        height: auto;
        padding: 1 0 0 0;
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
    }

    #app-version {
        width: auto;
        height: 1;
        color: $text-muted;
        text-align: right;
    }

    .message-content {
        width: 1fr;
    }

    #chat-messages > MessageBubble.user .message-content {
        color: $text-muted;
    }
    #chat-messages > MessageBubble.assistant .message-content {
        color: $text;
    }

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
        self._llm_model = ""
        self._model_set_at: float = 0.0
        self._temperature: float = 0.3
        self._max_tokens: int = 1024
        self._system_prompt: str = DEFAULT_SYSTEM_PROMPT
        self._request_timeout: int = 240
        self._context_window_turns: int = 12
        self._slash_commands: list[_config.SlashCommand] = []
        self._status_widget: Static | None = None

    def is_streaming(self) -> bool:
        return self._streaming_active

    def compose(self) -> ComposeResult:
        with VerticalScroll(id="chat-messages"):
            yield MessageBubble("assistant", self._build_help_text(), show_role=False)

        with Vertical(id="chat-input-row"):
            yield TextArea(
                placeholder="Type a message... (or /grammar, /help, ...)",
                id="chat-input",
                soft_wrap=True,
            )
            with Horizontal(id="chat-footer-row"):
                yield Static("", id="connection-status")
                yield Static(f"v{version.APP_VERSION}", id="app-version")

    def on_mount(self) -> None:
        self._status_widget = self.query_one("#connection-status", Static)
        self._refresh_config()
        self.set_interval(10.0, self._refresh_config)

    # ---- Config refresh ----

    def _refresh_config(self) -> None:
        """Read config and FLM status directly (no daemon)."""
        try:
            cfg = engine.build_config_snapshot()
        except Exception as exc:
            log.warning("config_snapshot failed: %s", exc)
            return

        self._llm_base_url = str(cfg.get("flm_api", {}).get("url") or self._llm_base_url)
        daemon_model = str(cfg.get("flm_server", {}).get("model") or "")
        model_loaded = bool(cfg.get("flm_server", {}).get("flm_model_loaded", False))
        recent_push = time.monotonic() - self._model_set_at <= 60.0

        if daemon_model:
            if not recent_push:
                self._llm_model = daemon_model
            model_display = self._llm_model if (model_loaded or recent_push) else "(none)"
        else:
            model_display = "(none)"

        chat_cfg = cfg.get("chat") or {}
        self._temperature = float(chat_cfg.get("temperature") or self._temperature)
        self._max_tokens = int(chat_cfg.get("max_tokens") or self._max_tokens)
        sp = str(chat_cfg.get("system_prompt") or "").strip()
        if sp:
            self._system_prompt = sp
        self._request_timeout = int(chat_cfg.get("request_timeout_s") or self._request_timeout)
        self._context_window_turns = int(chat_cfg.get("context_window_turns") or self._context_window_turns)

        # Read slash commands from config.
        try:
            app_cfg = _config.load_config()
            self._slash_commands = list(app_cfg.slash_commands)
        except Exception as exc:
            log.warning("could not load slash commands: %s", exc)

        status = f"Model: {model_display}"
        if self._status_widget is not None:
            self._status_widget.update(status)

    def set_model(self, model_name: str) -> None:
        self._llm_model = model_name
        self._model_set_at = time.monotonic()
        status = f"Model: {model_name if model_name else '(none)'}"
        if self._status_widget is not None:
            self._status_widget.update(status)

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

        if text.startswith("/"):
            self._handle_slash_command(text)
            return

        self._send_chat_message(text)

    # ---- Slash commands (config-driven) ----

    def _build_help_text(self) -> str:
        lines = ["[bold]Slash commands:[/]"]
        for c in self._slash_commands:
            desc = c.description or ""
            lines.append(f"  /{c.name} <text>  — {desc}")
        lines.append("  /clear           — Clear conversation history")
        lines.append("  /help            — Show this help")
        lines.append("")
        lines.append("[bold]Shortcuts:[/]")
        lines.append("  F1            — Chat")
        lines.append("  F2            — Dashboard")
        lines.append("  Ctrl+P        — Commands")
        lines.append("  Ctrl+C        — Quit (press twice)")
        return "\n".join(lines)

    def _handle_slash_command(self, text: str) -> None:
        parts = text.split(maxsplit=1)
        cmd = parts[0].lower()
        args = parts[1] if len(parts) > 1 else ""

        if cmd == "/clear":
            self._clear_history()
            return
        if cmd == "/help":
            self._add_message("assistant", self._build_help_text(), show_role=False)
            return

        cmd_name = cmd[1:]  # strip leading /
        command_cfg = next((c for c in self._slash_commands if c.name == cmd_name), None)
        if command_cfg is not None:
            if not args:
                args = self._get_last_selection()
            if args:
                user_label = f"/{cmd_name}: {args[:200]}{'…' if len(args) > 200 else ''}"
                self._send_chat_message(args, system_prompt_override=command_cfg.system_prompt, user_label=user_label)
            else:
                self._add_message("assistant", f"[yellow]No text provided for /{cmd_name}.[/]")
        else:
            self._add_message("assistant", f"Unknown command: {cmd}\n\nType /help for available commands.")

    # ---- Chat message sending ----

    def _send_chat_message(self, text: str, *,
                           system_prompt_override: str | None = None,
                           user_label: str | None = None) -> None:
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

        display_text = user_label or text
        self._add_message("user", display_text)
        self._add_message("assistant", "…", is_streaming=True)

        self._history.trim_to_window(self._context_window_turns, extra=2)
        history = self._history.to_payload()

        threading.Thread(
            target=self._stream_llm_response,
            args=(text, list(history)),
            kwargs={"system_prompt": system_prompt_override},
            daemon=True,
        ).start()

    def _stream_llm_response(self, user_text: str, history: list[dict], *,
                              system_prompt: str | None = None) -> None:
        """Stream LLM response via OpenAI-compatible SSE endpoint."""
        sp = system_prompt or self._system_prompt

        # Ensure the FLM server is running before attempting the chat request.
        if not engine.is_flm_server_reachable():
            try:
                result = engine.start_flm_server()
                log.info("start_flm_server: %s", result)
            except Exception as exc:
                self.call_later(self._finalize_stream,
                                f"[red]Could not start LLM server: {exc}[/]")
                return

        import urllib.request

        messages = [{"role": "system", "content": sp}]
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
            except Exception:
                pass
            err_msg = f"[red]LLM HTTP {exc.code}: {error_body}[/]"
            self.call_later(self._finalize_stream, err_msg)
        except Exception as exc:
            err_msg = f"[red]LLM error: {exc}[/]"
            self.call_later(self._finalize_stream, err_msg)

    # ---- Message management ----

    def _add_message(self, role: str, content: str, is_streaming: bool = False,
                     show_role: bool = True) -> None:
        msg = self._history.add(role, content)
        msg_id = msg["msg_id"]

        messages = self.query_one("#chat-messages", VerticalScroll)
        bubble = MessageBubble(role, content, is_streaming=is_streaming,
                               show_role=show_role, msg_id=msg_id)
        bubble.add_class(role)
        messages.mount(bubble)
        self.call_after_refresh(self._scroll_chat_to_bottom)

        if not is_streaming:
            self._current_bubble = None
        else:
            self._current_bubble = bubble
            self._streaming_active = True

    def _update_stream(self, content: str) -> None:
        if self._current_bubble is not None:
            self._current_bubble.update_content(content)
            self.call_after_refresh(self._scroll_chat_to_bottom)

    def _finalize_stream(self, content: str) -> None:
        if self._current_bubble is not None:
            self._current_bubble.update_content(content)
            self._current_bubble.finalize_stream()
            self._current_bubble = None

        self._streaming_active = False
        self._history.update_last_assistant(content)
        self.call_after_refresh(self._scroll_chat_to_bottom)

    def _scroll_chat_to_bottom(self) -> None:
        try:
            messages = self.query_one("#chat-messages", VerticalScroll)
            messages.scroll_end(animate=False)
        except Exception as exc:
            log.debug("could not scroll chat to bottom: %s", exc)

    def _clear_history(self) -> None:
        self._history.clear()
        messages = self.query_one("#chat-messages", VerticalScroll)
        messages.remove_children()
        self._add_message("assistant", "— conversation cleared —")

    def _get_last_selection(self) -> str:
        import pyperclip
        try:
            return pyperclip.paste()
        except Exception as exc:
            log.debug("clipboard paste failed: %s", exc)
            return ""

    # ---- Ingest (called by TUI app when launched with --ingest-file) ----

    def post_ingested_text(self, text: str) -> None:
        if text:
            self._send_chat_message(text)
