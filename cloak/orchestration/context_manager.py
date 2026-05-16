"""
context_manager.py — compress agent message history between rounds.
Keeps context under CONTEXT_TOKEN_LIMIT to prevent prompt bloat.
See DECISIONS.md §D6.
"""
from __future__ import annotations

import queue
import threading

import ollama

from cloak.config import AGENT_TIMEOUT, CONTEXT_TOKEN_LIMIT, MODEL_KEEP_ALIVE, MODEL_NUM_CTX, ORCHESTRATOR_MODEL

_SUMMARISE_PROMPT = """\
Summarise the following conversation between a document parser agent and its tools.
Keep ALL important facts, values, measurements, section names, and key terms mentioned.
Write a concise paragraph (≤200 words) that a future agent can use to understand
what has been extracted so far and what gaps remain.
Do not add interpretation — only facts from the conversation."""


def estimate_tokens(messages: list[dict]) -> int:
    """Rough token estimate: total chars / 4."""
    return sum(len(str(m.get("content", ""))) for m in messages) // 4


def _summarise(messages: list[dict], model: str = ORCHESTRATOR_MODEL) -> str:
    """Call the orchestrator model to summarise a message slice."""
    conversation = "\n".join(
        f"{m['role'].upper()}: {m.get('content', '')}" for m in messages
    )
    result_q: queue.Queue = queue.Queue()

    def _worker() -> None:
        try:
            resp = ollama.chat(
                model=model,
                messages=[
                    {"role": "system", "content": _SUMMARISE_PROMPT},
                    {"role": "user",   "content": conversation[:12000]},
                ],
                options={"temperature": 0.1, "num_ctx": MODEL_NUM_CTX},
                keep_alive=MODEL_KEEP_ALIVE,
            )
            result_q.put(("ok", resp.message.content.strip()))
        except Exception as exc:
            result_q.put(("err", exc))

    t = threading.Thread(target=_worker, daemon=True)
    t.start()
    try:
        kind, value = result_q.get(timeout=AGENT_TIMEOUT)
    except queue.Empty:
        return "[Summary unavailable — timeout]"

    if kind == "err":
        return f"[Summary unavailable — {value}]"
    return value


def compress_history(
    messages: list[dict],
    token_limit: int = CONTEXT_TOKEN_LIMIT,
    model: str = ORCHESTRATOR_MODEL,
) -> list[dict]:
    """
    Compress messages when estimated token count exceeds token_limit.

    Preservation rules:
      - messages[0] (system prompt) always kept intact
      - last 2 user/assistant exchanges always kept intact
      - everything between is summarised into a single system message
    """
    if estimate_tokens(messages) <= token_limit:
        return messages

    if len(messages) <= 5:
        return messages

    system_prompt = messages[0]
    tail = messages[-4:] if len(messages) >= 4 else messages[-2:]
    middle = messages[1: len(messages) - len(tail)]

    if not middle:
        return messages

    summary_text = _summarise(middle, model=model)
    summary_msg  = {
        "role":    "system",
        "content": f"[Prior context summary]\n{summary_text}",
    }

    return [system_prompt, summary_msg] + tail
