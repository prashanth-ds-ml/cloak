"""
model_router.py — phase-based sequential model routing for 8 GB GPU.

Two hard phases per round (D14):
  VISION PHASE       — extract + judge  (qwen2.5vl:7b or llama3.2-vision:11b)
  ORCHESTRATOR PHASE — patch loop       (qwen3:8b)

Phase boundaries are called explicitly by parser_agent via:
  before_vision_phase()       — unloads qwen3:8b when llama3.2-vision is sticky
  before_orchestrator_phase() — unloads llama3.2-vision when it was sticky

qwen2.5vl:7b + qwen3:8b can coexist on 24 GB RAM (~10 GB total, GPU+RAM spill).
llama3.2-vision:11b (11 GB) cannot coexist with qwen3:8b — enforced by D7.
"""
from __future__ import annotations

import httpx

from cloak.config import (
    OLLAMA_BASE_URL,
    ORCHESTRATOR_MODEL,
    VISION_FALLBACK,
    VISION_PRIMARY,
)


# ── Ollama management ─────────────────────────────────────────────────────────

def loaded_models() -> list[str]:
    """Return names of models currently loaded in Ollama (via GET /api/ps)."""
    try:
        resp = httpx.get(f"{OLLAMA_BASE_URL}/api/ps", timeout=5)
        resp.raise_for_status()
        return [m["name"] for m in resp.json().get("models", [])]
    except Exception:
        return []


def unload(model: str) -> None:
    """Unload a model from Ollama memory by setting keep_alive=0."""
    try:
        httpx.post(
            f"{OLLAMA_BASE_URL}/api/generate",
            json={"model": model, "keep_alive": 0},
            timeout=10,
        )
    except Exception:
        pass


# ── Sticky model per PDF ──────────────────────────────────────────────────────

_sticky_vision: str | None = None
_using_fallback: bool = False


def reset() -> None:
    """Call at the start of each PDF to clear sticky model state."""
    global _sticky_vision, _using_fallback
    _sticky_vision = None
    _using_fallback = False


# ── Public API ────────────────────────────────────────────────────────────────

def get_vision_model() -> str:
    return _sticky_vision or VISION_PRIMARY


def mark_success(model: str) -> None:
    global _sticky_vision
    _sticky_vision = model


def switch_to_fallback() -> None:
    """Kept for compatibility — prefer before_vision_phase() in new code."""
    global _sticky_vision, _using_fallback
    if _using_fallback:
        return
    currently = loaded_models()
    if ORCHESTRATOR_MODEL in currently:
        unload(ORCHESTRATOR_MODEL)
    _sticky_vision = VISION_FALLBACK
    _using_fallback = True


def before_vision_phase() -> None:
    """
    Phase boundary: unload qwen3:8b when llama3.2-vision is the sticky model (D7/D14).
    No-op when qwen2.5vl:7b is sticky — those two coexist safely.
    """
    if _sticky_vision == VISION_FALLBACK:
        if ORCHESTRATOR_MODEL in loaded_models():
            unload(ORCHESTRATOR_MODEL)


def before_orchestrator_phase() -> None:
    """
    Phase boundary: unload llama3.2-vision before the patch loop (D7/D14).
    Resets sticky so region_describe calls inside the patch loop use VISION_PRIMARY.
    No-op when qwen2.5vl:7b is sticky.
    """
    global _sticky_vision, _using_fallback
    if _sticky_vision == VISION_FALLBACK:
        unload(VISION_FALLBACK)
        _sticky_vision = None
        _using_fallback = False


def restore_orchestrator() -> None:
    """Kept for compatibility — prefer before_orchestrator_phase() in new code."""
    global _using_fallback
    if not _using_fallback:
        return
    unload(VISION_FALLBACK)
    _using_fallback = False


def using_fallback() -> bool:
    return _using_fallback


def teardown_pdf() -> None:
    """Unload the vision model at end of PDF. Keep orchestrator warm."""
    if _sticky_vision and _sticky_vision != ORCHESTRATOR_MODEL:
        unload(_sticky_vision)
    reset()
