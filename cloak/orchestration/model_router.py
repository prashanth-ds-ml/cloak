"""
model_router.py — phase-based sequential model routing.

Ollama auto-splits any model across GPU + CPU RAM when VRAM is insufficient.
We use total available memory (VRAM + RAM) to decide model viability — never
reject a model purely because it doesn't fit in GPU alone.

Two hard phases per round (D14):
  VISION PHASE       — extract + judge  (qwen2.5vl:7b or qwen3-vl:4b)
  ORCHESTRATOR PHASE — format + patch   (qwen3:8b)

Phase boundaries unload the inactive model so the active one has maximum
memory for its auto-split. With MODEL_KEEP_ALIVE=-1 models stay warm within
a phase; explicit unload at the boundary frees memory for the next phase.

ParsePlan routing (D28):
  set_parse_plan(plan) — called after Phase 1; vision_models_to_try() uses
  plan.model_tier to skip the probe entirely ("none"), use fallback only
  ("fallback"), or full primary→fallback chain ("primary").
"""
from __future__ import annotations

import subprocess
from typing import TYPE_CHECKING, Any

import httpx
import psutil

from cloak.config import (
    OLLAMA_BASE_URL,
    ORCHESTRATOR_MODEL,
    VISION_FALLBACK,
    VISION_PRIMARY,
)

if TYPE_CHECKING:
    from cloak.profiling.doc_profiler import ParsePlan


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

_sticky_vision:  str | None = None
_using_fallback: bool       = False
_current_plan:   Any        = None   # ParsePlan | None — set by set_parse_plan()


def reset() -> None:
    """Call at the start of each PDF to clear sticky model state and ParsePlan."""
    global _sticky_vision, _using_fallback, _current_plan
    _sticky_vision  = None
    _using_fallback = False
    _current_plan   = None


def set_parse_plan(plan: ParsePlan) -> None:
    """Store the ParsePlan for the current PDF. Called during Phase 1 before probe."""
    global _current_plan
    _current_plan = plan


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
    Phase boundary: unload orchestrator before vision phase (D14).
    Frees its GPU layers so vision model gets maximum memory for auto-split.
    """
    if ORCHESTRATOR_MODEL in loaded_models():
        unload(ORCHESTRATOR_MODEL)


def before_orchestrator_phase() -> None:
    """
    Phase boundary: unload vision model before orchestrator phase (D14).
    Frees its GPU layers so qwen3:8b gets maximum memory for auto-split.
    Sticky model is preserved — remembered for the next vision phase.
    """
    if _sticky_vision and _sticky_vision in loaded_models():
        unload(_sticky_vision)


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


# Model weight sizes — used for total-memory viability check
# Ollama auto-splits across GPU+RAM; a model is viable if total >= size
_MODEL_SIZE_GB: dict[str, float] = {
    VISION_PRIMARY:     7.3,   # qwen2.5vl:7b
    VISION_FALLBACK:    3.5,   # qwen3-vl:4b
    ORCHESTRATOR_MODEL: 5.2,   # qwen3:8b
}


def _free_vram_gb() -> float:
    """Quick nvidia-smi probe. Returns 0.0 on any failure."""
    try:
        r = subprocess.run(
            ["nvidia-smi", "--query-gpu=memory.free", "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=5,
        )
        if r.returncode == 0:
            return float(r.stdout.strip().splitlines()[0]) / 1024  # MiB → GB
    except Exception:
        pass
    return 0.0


def _free_ram_gb() -> float:
    """Available system RAM in GB. Returns 0.0 on failure."""
    try:
        return psutil.virtual_memory().available / 1e9
    except Exception:
        return 0.0


def vision_models_to_try() -> list[str]:
    """
    Return vision models to probe in quality-first preference order (D28).

    ParsePlan model_tier overrides hardware check:
      "none"     → [] (no visual content — skip probe entirely)
      "fallback" → [VISION_FALLBACK]
      "primary"  → hardware-aware selection below

    Hardware check uses total available memory (VRAM + RAM).
    Ollama auto-splits any model across GPU and CPU RAM, so a model is
    viable whenever total_free >= model_weight_gb.
    Models are ordered primary-first; only included if total memory covers them.
    """
    if _current_plan is not None:
        tier = getattr(_current_plan, "model_tier", "primary")
        if tier == "none":
            return []
        if tier == "fallback":
            return [VISION_FALLBACK]

    free_vram = _free_vram_gb()
    free_ram  = _free_ram_gb()
    total     = free_vram + free_ram

    models: list[str] = []
    for m in (VISION_PRIMARY, VISION_FALLBACK):
        if total >= _MODEL_SIZE_GB.get(m, 9.9):
            models.append(m)
    return models


