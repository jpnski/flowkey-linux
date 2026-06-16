"""Text normalization and utility helpers for LLM client calls."""

from __future__ import annotations

import logging
import re

log = logging.getLogger("ffchat.llm")


def reset_usage_acc(usage_acc: dict) -> None:
    usage_acc.clear()
    usage_acc["prompt_tokens"] = 0
    usage_acc["completion_tokens"] = 0


def snapshot_usage_acc(usage_acc: dict) -> dict:
    return {
        "prompt_tokens": usage_acc.get("prompt_tokens", 0),
        "completion_tokens": usage_acc.get("completion_tokens", 0),
    }


def normalize_output(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def dict_protect(text: str, protected_words: list[str]) -> tuple[str, dict[str, str]]:
    """Replace protected words with placeholders; return (obfuscated, mapping)."""
    mapping: dict[str, str] = {}
    if not protected_words:
        return text, mapping
    pattern = re.compile(
        r"(\b(?:" + "|".join(re.escape(w) for w in protected_words) + r")\b)",
        re.IGNORECASE,
    )
    obfuscated = []
    for token in pattern.split(text):
        if pattern.fullmatch(token):
            key = "<DICT_PROTECT_" + str(len(mapping)) + ">"
            mapping[key] = token
            obfuscated.append(key)
        else:
            obfuscated.append(token)
    return "".join(obfuscated), mapping


def dict_restore(text: str, mapping: dict[str, str]) -> str:
    """Replace placeholders produced by dict_protect with original words."""
    if not mapping:
        return text
    for placeholder, original in mapping.items():
        text = text.replace(placeholder, original)
    return text
