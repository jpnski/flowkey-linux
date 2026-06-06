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
import uuid
from pathlib import Path

import loopback_http
from textual.app import ComposeResult
from textual.containers import Container, Horizontal, Vertical, VerticalScroll
from textual.widgets import Button, Input, Static

log = logging.getLogger("flowkey.tui.chat")

DAEMON_BASE_URL = "http://127.0.0.1:52650"

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
[b]Flowkey TUI Chat[/b]

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
  Enter              — Send message
  Ctrl+P             — Command palette
  Ctrl+Q             — Quit
"""


# ---------------------------------------------------------------------------
# Message bubble widget
# ---------------------------------------------------------------------------


class MessageBubble(Static):
    """A single chat message with role-based styling."""

    def __init__(self, role: str, content: str, is_streaming: bool = False) -> None:
        self._role = role
        self._content = content
        self._is_streaming = is_streaming
        rendered = self._format_content()
        super().__init__(rendered)

    def _format_content(self) -> str:
        """Format role + content for display."""
        role_tag = "You" if self._role == "user" else "Flowkey"
        if self._is_streaming:
            role_tag += " (streaming)"
        # Only escape markup brackets for untrusted user input.
        # Assistant content (HELP_TEXT, LLM responses) may use [b]/[i] etc.
        content = self._content
        if self._role == "user":
            content = content.replace("[", "[[")
        return f"[bold]{role_tag}:[/]\n\n{content}"

    def update_content(self, content: str) -> None:
        """Update content in-place (for streaming)."""
        self._content = content
        self.update(self._format_content())

    def finalize_stream(self) -> None:
        """Mark streaming as complete."""
        self._is_streaming = False
        self.update(self._format_content())


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
        margin: 0 0 1 0;
        padding: 1;
        min-height: 1;
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
        padding: 0 1;
        background: $surface;
    }

    #chat-input-row > Horizontal {
        height: 3;
    }

    #chat-input {
        width: 1fr;
    }

    #send-btn {
        width: 10;
        margin-left: 1;
    }

    #connection-status {
        width: 1fr;
        height: 1;
        color: $text-muted;
    }
    """

    def __init__(self) -> None:
        super().__init__()
        self._history: list[dict] = []
        self._thread_id: str = uuid.uuid4().hex
        self._streaming_active = False
        self._current_bubble: MessageBubble | None = None
        self._llm_base_url = "http://127.0.0.1:52625"
        self._llm_model = "gemma4-it:e4b"
        self._daemon_available = False
        self._lock = threading.Lock()

    def compose(self) -> ComposeResult:
        with VerticalScroll(id="chat-messages"):
            yield MessageBubble("assistant", HELP_TEXT)

        with Vertical(id="chat-input-row"):
            with Horizontal():
                yield Input(placeholder="Type a message... (or /grammar, /help, ...)", id="chat-input")
                yield Button("Send", id="send-btn", variant="primary")
            yield Static("", id="connection-status")

    def on_mount(self) -> None:
        self._refresh_config()
        self.set_interval(30.0, self._refresh_config)

    # ---- Config refresh ----

    def _refresh_config(self) -> None:
        """Pull live config from daemon."""
        try:
            resp = loopback_http.json_post(
                f"{DAEMON_BASE_URL}/action/config_snapshot",
                {"args": {}},
                headers=loopback_http.daemon_headers(),
                timeout=2.0,
            )
            if resp.get("ok") and isinstance(resp.get("result"), dict):
                result = resp["result"]
                self._llm_base_url = str(result.get("flm_base_url") or self._llm_base_url)
                self._llm_model = str(result.get("flm_model") or self._llm_model)
                self._daemon_available = True
                self._update_status(f"Model: {self._llm_model}  |  Daemon: connected")
                return
        except Exception:
            pass
        self._daemon_available = False
        self._update_status("Daemon: not connected — config may be stale")

    def _update_status(self, text: str) -> None:
        status = self.query_one("#connection-status", Static)
        status.update(text)

    # ---- Input handling ----

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id == "chat-input":
            self._process_input(event.value)
            event.input.clear()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "send-btn":
            inp = self.query_one("#chat-input", Input)
            text = inp.value
            if text:
                self._process_input(text)
                inp.clear()

    # ---- Message processing ----

    def _process_input(self, text: str) -> None:
        text = text.strip()
        if not text:
            return
        if self._streaming_active:
            return

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
            self._add_message("assistant", HELP_TEXT)
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

    def _send_chat_message(self, text: str) -> None:
        """Send a regular chat message to the LLM with streaming."""
        self._add_message("user", text)
        self._add_message("assistant", "…", is_streaming=True)

        history = [{"role": m["role"], "content": m["content"]} for m in self._history]

        threading.Thread(
            target=self._stream_llm_response,
            args=(text, list(history)),
            daemon=True,
        ).start()

    def _stream_llm_response(self, user_text: str, history: list[dict]) -> None:
        """Stream LLM response via OpenAI-compatible SSE endpoint."""
        import urllib.request

        messages = [{"role": "system", "content": "You are a concise, helpful local assistant."}]
        messages.extend(history)

        body = json.dumps({
            "model": self._llm_model,
            "messages": messages,
            "stream": True,
            "temperature": 0.3,
            "max_tokens": 1024,
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
            with urllib.request.urlopen(req, timeout=240) as resp:
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
            except Exception:
                pass
            err_msg = f"[red]LLM HTTP {exc.code}: {error_body}[/]"
            self.call_later(self._finalize_stream, err_msg)
        except Exception as exc:
            err_msg = f"[red]LLM error: {exc}[/]"
            self.call_later(self._finalize_stream, err_msg)

    # ---- Message management ----

    def _add_message(self, role: str, content: str, is_streaming: bool = False) -> None:
        """Add a message to the chat log."""
        msg = {"role": role, "content": content, "timestamp": time.time()}
        with self._lock:
            self._history.append(msg)

        messages = self.query_one("#chat-messages", VerticalScroll)
        bubble = MessageBubble(role, content, is_streaming=is_streaming)
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
        if self._history:
            last = self._history[-1]
            if last["role"] in ("assistant", self._history[-1]["role"]):
                last["content"] = content

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
        except Exception:
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
