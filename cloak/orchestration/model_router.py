"""
model_router.py — phase-based two-model routing.

Two-model mode (D49):
  VISION_PRIMARY (qwen3-vl:8b, 6.1 GB) — VLM for figures, image pages, L4 judge.
  ORCHESTRATOR_MODEL (qwen3:14b, 9.0 GB) — LLM for FORMAT, PATCH, deep review.

  VLM and LLM are mutually exclusive in VRAM on the RTX 5050 (8 GB):
    6.1 + 9.0 = 15.1 GB — simultaneous load would force LLM entirely onto slow RAM.
    Phase boundaries enforce the swap using confirmed unload (D50):
      before_vision_phase()       — unloads LLM, waits for VRAM release, VLM loads lazily
      before_orchestrator_phase() — unloads VLM, waits for VRAM release, LLM loads lazily

  glm-ocr (2.2 GB) is always-resident during parse (D45):
    Coexists with VLM: 6.1 + 2.2 = 8.3 GB (0.3 GB spills to RAM — fine)
    Coexists with LLM: 9.0 + 2.2 = 11.2 GB (glm-ocr goes to RAM — fine)
    Unloaded only by teardown_pdf().

  Phase 9 reuse (D49): LLM stays loaded from Phase 6 through Phase 9.
  teardown_pdf() is called AFTER Phase 9, not before.

Confirmed unload (D50):
  unload_and_wait() polls /api/ps until model disappears before returning.
  Prevents OOM from two large models competing for the same 8 GB VRAM.

ParsePlan routing (D28):
  set_parse_plan(plan) — called after Phase 1; vision_models_to_try() uses
  plan.model_tier to skip the probe ("none"), fallback only ("fallback"),
  or full primary->fallback chain ("primary").
"""
from __future__ import annotations

import subprocess
import time
from typing import TYPE_CHECKING, Any

import httpx
import psutil

from cloak.config import (
    GLM_OCR_MODEL,
    OLLAMA_BASE_URL,
    ORCHESTRATOR_MODEL,
    VISION_FALLBACK,
    VISION_PRIMARY,
)

if TYPE_CHECKING:
    from cloak.profiling.doc_profiler import ParsePlan


# ── Ollama management ─────────────────────────────────────────────────────────

def is_ollama_available() -> bool:
    """Fast health check — True when Ollama server is reachable."""
    try:
        resp = httpx.get(f"{OLLAMA_BASE_URL}/api/version", timeout=3)
        return resp.status_code == 200
    except Exception:
        return False


def loaded_models() -> list[str]:
    """Return names of models currently loaded in Ollama (via GET /api/ps)."""
    try:
        resp = httpx.get(f"{OLLAMA_BASE_URL}/api/ps", timeout=5)
        resp.raise_for_status()
        return [m["name"] for m in resp.json().get("models", [])]
    except Exception:
        return []


def unload(model: str) -> None:
    """Fire-and-forget unload. Use unload_and_wait() at phase boundaries (D50)."""
    try:
        httpx.post(
            f"{OLLAMA_BASE_URL}/api/generate",
            json={"model": model, "keep_alive": 0},
            timeout=10,
        )
    except Exception:
        pass


def unload_and_wait(model: str, timeout: float = 30.0) -> None:
    """
    Unload model and poll /api/ps until Ollama confirms it is gone (D50).

    Prevents OOM at phase boundaries: with VLM (6.1 GB) and LLM (9.0 GB), if
    the incoming model starts loading before the outgoing one releases VRAM,
    both compete for 8 GB simultaneously. The 0.5s post-confirmation pause
    gives the CUDA allocator time to return pages before the next load.

    Falls through after timeout with a warning rather than blocking forever.
    """
    unload(model)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if model not in loaded_models():
            time.sleep(0.5)   # CUDA allocator needs a moment to return pages
            return
        time.sleep(1.0)
    # timeout — model may still be in VRAM; next load will be slower but parse continues


# ── Sticky model per PDF ──────────────────────────────────────────────────────

_sticky_vision:  str | None = None
_using_fallback: bool       = False
_current_plan:   Any        = None   # ParsePlan | None


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
    if ORCHESTRATOR_MODEL in loaded_models():
        unload_and_wait(ORCHESTRATOR_MODEL)
    _sticky_vision = VISION_FALLBACK
    _using_fallback = True


def before_vision_phase() -> None:
    """
    Phase boundary: unload LLM before VLM phase so VLM has full VRAM (D49).

    VLM (6.1 GB) + LLM (9.0 GB) = 15.1 GB — simultaneous load forces LLM onto
    slow CPU RAM. Uses confirmed unload (D50) to ensure VRAM is free before the
    VLM loads lazily on its first ollama.chat() call.
    """
    if ORCHESTRATOR_MODEL in loaded_models():
        unload_and_wait(ORCHESTRATOR_MODEL)


def before_orchestrator_phase() -> None:
    """
    Phase boundary: unload VLM before LLM phase (D49).

    Uses confirmed unload (D50). Sticky VLM variable is preserved so the next
    before_vision_phase() call knows which model to mark as loaded.
    glm-ocr is NOT unloaded — it coexists with the LLM (11.2 GB total across
    GPU+RAM, well within the 32 GB pool).
    """
    if _sticky_vision and _sticky_vision in loaded_models():
        unload_and_wait(_sticky_vision)


def restore_orchestrator() -> None:
    """Kept for compatibility — prefer before_orchestrator_phase() in new code."""
    global _using_fallback
    if not _using_fallback:
        return
    if _sticky_vision and _sticky_vision in loaded_models():
        unload_and_wait(_sticky_vision)
    _using_fallback = False


def using_fallback() -> bool:
    return _using_fallback


def teardown_pdf() -> None:
    """
    Unload all pipeline models at end of PDF in safe order (D49).

    Called AFTER Phase 9 — LLM is reused for deep review, so teardown must
    not fire before _run_phase9() returns. Order: VLM -> LLM -> glm-ocr,
    each with confirmed wait (D50).
    """
    for model in (_sticky_vision, ORCHESTRATOR_MODEL, GLM_OCR_MODEL):
        if model and model in loaded_models():
            unload_and_wait(model)
    reset()


# ── Model sizes — used for total-memory viability check ───────────────────────

# Ollama auto-splits across GPU+RAM; a model is viable if total >= size (D32).
_MODEL_SIZE_GB: dict[str, float] = {
    VISION_PRIMARY:      6.1,   # qwen3-vl:8b — full GPU on RTX 5050 (D49)
    VISION_FALLBACK:     3.3,   # qwen3-vl:4b — full GPU, always fits (D49)
    ORCHESTRATOR_MODEL:  9.0,   # qwen3:14b — ~8 GB GPU + ~1 GB RAM (D49)
    GLM_OCR_MODEL:       2.2,   # glm-ocr — always-resident, coexists with either (D45)
}


def _free_vram_gb() -> float:
    """Quick nvidia-smi probe. Returns 0.0 on any failure."""
    try:
        r = subprocess.run(
            ["nvidia-smi", "--query-gpu=memory.free", "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=5,
        )
        if r.returncode == 0:
            return float(r.stdout.strip().splitlines()[0]) / 1024  # MiB -> GB
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
      "none"     -> [] (no visual content — skip probe entirely)
      "fallback" -> [VISION_FALLBACK]
      "primary"  -> hardware-aware selection below

    Hardware check uses total available memory (VRAM + RAM) — D32.
    VLM is probed after LLM is unloaded (before_vision_phase called first),
    so free VRAM reflects the actual budget available to the VLM.
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
