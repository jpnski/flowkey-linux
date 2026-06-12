"""Tool-calling helpers for FastFlowLM (prototype).

FastFlowLM 0.9.43's gemma tool-call serialization is broken (500 on every real
invocation). The `chat_with_notes_context` function works around this by doing
note_search client-side and injecting results into the prompt.
"""

from __future__ import annotations

import json
import logging
import urllib.request

log = logging.getLogger("flowkey.tools")

DEFAULT_BASE_URL = "http://127.0.0.1:52625"
DEFAULT_MODEL = "gemma4-it:e4b"


def _dispatch(name: str, arguments: dict) -> dict:
    if name == "note_search":
        import notes
        query = str(arguments.get("query") or "").strip()
        try:
            limit = int(arguments.get("limit") or 5)
        except (TypeError, ValueError):
            limit = 5
        res = notes.search_notes(query, limit)
        # Compact the result for the model: title + category + snippet only.
        items = [
            {"title": r["title"], "category": r["category"], "snippet": r["snippet"]}
            for r in res.get("results", [])
        ]
        return {"query": res.get("query"), "count": res.get("count", 0), "results": items}
    return {"error": f"unknown tool: {name}"}


def run_tool_call(name: str, arguments_json) -> dict:
    """Execute a tool by name. `arguments_json` may be a JSON string or dict."""
    if isinstance(arguments_json, str):
        try:
            args = json.loads(arguments_json)
        except ValueError as exc:
            log.debug("tool-call arguments were not valid JSON (%r): %s", arguments_json, exc)
            args = {}
    else:
        args = arguments_json or {}
    if not isinstance(args, dict):
        args = {}
    return _dispatch(name, args)


def _post_chat(base_url: str, model: str, messages: list, *, tools=None, timeout: int = 60) -> dict:
    # NOTE: do NOT send "tool_choice" — FastFlowLM's gemma tool template rejects
    # it with HTTP 500 ("type must be string, but is object"). The documented
    # format is just a `tools` array of objects with object `parameters`.
    body: dict = {"model": model, "messages": messages, "temperature": 0.2}
    if tools:
        body["tools"] = tools
    req = urllib.request.Request(
        base_url.rstrip("/") + "/v1/chat/completions",
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8", errors="replace"))





def chat_with_notes_context(
    user_text: str,
    *,
    base_url: str = DEFAULT_BASE_URL,
    model: str = DEFAULT_MODEL,
    limit: int = 5,
    timeout: int = 120,
) -> dict:
    """Note-augmented answer using client-side note_search.

    FastFlowLM 0.9.43's gemma tool-call serialization 500s on every real tool
    invocation, so this client-side path runs note_search itself, injects the
    top matches as context, and asks the model to answer from them.

    Returns {text, notes_used: [{title, category}], count}.
    """
    result = run_tool_call("note_search", {"query": user_text, "limit": limit})
    hits = result.get("results", [])
    if hits:
        ctx_lines = []
        for i, h in enumerate(hits, 1):
            ctx_lines.append(f"[{i}] {h['title']} (category: {h['category']})\n    {h['snippet']}")
        context = "\n".join(ctx_lines)
        system = (
            "You answer using the user's personal notes provided below. Cite the "
            "note titles you used. If the notes do not contain the answer, say so."
        )
        user = f"Notes found for this question:\n{context}\n\nQuestion: {user_text}"
    else:
        system = "You are a helpful assistant."
        user = f"(No matching notes were found.)\n\nQuestion: {user_text}"
    messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]
    resp = _post_chat(base_url, model, messages, tools=None, timeout=timeout)
    message = (resp.get("choices") or [{}])[0].get("message") or {}
    return {
        "text": (message.get("content") or "").strip(),
        "notes_used": [{"title": h["title"], "category": h["category"]} for h in hits],
        "count": result.get("count", 0),
    }
