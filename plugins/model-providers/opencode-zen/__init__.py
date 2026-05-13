"""OpenCode provider profiles (Zen + Go).

Both use per-model api_mode routing:
  - OpenCode Zen: Claude → anthropic_messages, GPT-5/Codex → codex_responses,
    everything else → chat_completions (this profile)
  - OpenCode Go: MiniMax → anthropic_messages, GLM/Kimi → chat_completions
    (this profile)
"""

from __future__ import annotations

import copy
from typing import Any

from providers import register_provider
from providers.base import ProviderProfile


def _text_from_openai_parts(parts: list[Any]) -> str:
    """Collapse OpenAI multimodal content parts to text-only content.

    OpenCode Go can route DeepSeek models. Its DeepSeek backend rejects
    OpenAI vision parts such as ``{"type": "image_url"}`` with a schema
    error that expects text-only parts.  Preserve all user-visible text and
    replace images with a compact placeholder/URL so text-only DeepSeek routes
    can continue tool-heavy conversations instead of failing the whole run.
    """
    out: list[str] = []
    for part in parts:
        if not isinstance(part, dict):
            if part is not None:
                out.append(str(part))
            continue
        ptype = part.get("type")
        if ptype in {"text", "input_text", "output_text"}:
            text = part.get("text")
            if isinstance(text, str) and text:
                out.append(text)
            continue
        if ptype in {"image_url", "input_image"}:
            image_value = part.get("image_url") or part.get("image") or {}
            url = image_value.get("url") if isinstance(image_value, dict) else image_value
            if isinstance(url, str) and url:
                out.append(f"[image omitted: {url}]")
            else:
                out.append("[image omitted]")
            continue
        text = part.get("text")
        if isinstance(text, str) and text:
            out.append(text)
    return "\n".join(piece for piece in out if piece).strip()


class OpenCodeGoProfile(ProviderProfile):
    """OpenCode Go — text-only fallback for DeepSeek-compatible routes."""

    def prepare_messages(self, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        needs_text_only = any(
            isinstance(msg, dict)
            and isinstance(msg.get("content"), list)
            and any(isinstance(part, dict) and part.get("type") in {"image_url", "input_image"} for part in msg.get("content", []))
            for msg in messages
        )
        if not needs_text_only:
            return messages

        sanitized = copy.deepcopy(messages)
        for msg in sanitized:
            if not isinstance(msg, dict):
                continue
            content = msg.get("content")
            if isinstance(content, list):
                msg["content"] = _text_from_openai_parts(content)
        return sanitized


opencode_zen = ProviderProfile(
    name="opencode-zen",
    aliases=("opencode", "opencode_zen", "zen"),
    env_vars=("OPENCODE_ZEN_API_KEY",),
    base_url="https://opencode.ai/zen/v1",
    default_aux_model="gemini-3-flash",
)

opencode_go = OpenCodeGoProfile(
    name="opencode-go",
    aliases=("opencode_go", "go", "opencode-go-sub"),
    env_vars=("OPENCODE_GO_API_KEY",),
    base_url="https://opencode.ai/zen/go/v1",
    default_aux_model="glm-5",
)

register_provider(opencode_zen)
register_provider(opencode_go)
