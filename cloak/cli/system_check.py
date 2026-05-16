"""
system_check.py — hardware probe + model readiness display.

Called by the CLI before every command to show the startup screen.
All functions are safe to call anytime — they never raise; failures return
sentinel values (0.0, "unknown", empty list) and display a warning instead.
"""
from __future__ import annotations

import io
import subprocess
import sys
import time

import httpx
import psutil
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

# Force UTF-8 stdout on Windows so unicode symbols don't crash in cp1252 terminals
if sys.platform == "win32" and hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

from cloak.config import (
    MIN_FREE_RAM_GB,
    OLLAMA_BASE_URL,
    ORCHESTRATOR_MODEL,
    VISION_FALLBACK,
    VISION_PRIMARY,
)

console = Console(legacy_windows=False)

# Minimum free RAM each model needs (GB) — used when no GPU is present
_MODEL_RAM_GB: dict[str, float] = {
    VISION_PRIMARY:     9.0,   # qwen2.5vl:7b
    ORCHESTRATOR_MODEL: 5.5,   # qwen3:8b
    VISION_FALLBACK:    11.0,  # llama3.2-vision:11b
}

# Approximate VRAM required at Q4_K_M quantization (±0.5 GB)
# Models run on GPU when VRAM is sufficient; Ollama auto-splits to CPU+GPU otherwise
_MODEL_VRAM_GB: dict[str, float] = {
    VISION_PRIMARY:     7.3,   # qwen2.5vl:7b — vision encoder makes it ~7.3 GB
    ORCHESTRATOR_MODEL: 5.2,   # qwen3:8b
    VISION_FALLBACK:    8.0,   # llama3.2-vision:11b
}

_MODEL_ROLE: dict[str, str] = {
    VISION_PRIMARY:     "vision primary",
    ORCHESTRATOR_MODEL: "orchestrator",
    VISION_FALLBACK:    "vision fallback",
}


# ── Hardware probes ───────────────────────────────────────────────────────────

def get_total_ram_gb() -> float:
    try:
        return psutil.virtual_memory().total / 1e9
    except Exception:
        return 0.0


def get_free_ram_gb() -> float:
    try:
        return psutil.virtual_memory().available / 1e9
    except Exception:
        return 0.0


def _nvidia_smi(query: str) -> str:
    """Run nvidia-smi with a single --query-gpu field. Returns raw value string or ''."""
    try:
        result = subprocess.run(
            ["nvidia-smi", f"--query-gpu={query}", "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode == 0:
            lines = result.stdout.strip().splitlines()
            return lines[0].strip() if lines else ""
    except (subprocess.TimeoutExpired, FileNotFoundError):
        pass
    return ""


def get_gpu_name() -> str:
    name = _nvidia_smi("name")
    return name if name else "unknown"


def get_total_vram_gb() -> float:
    raw = _nvidia_smi("memory.total")
    try:
        return float(raw) / 1024  # MiB → GB
    except ValueError:
        return 0.0


def get_free_vram_gb() -> float:
    raw = _nvidia_smi("memory.free")
    try:
        return float(raw) / 1024
    except ValueError:
        return 0.0


# ── Ollama probe ──────────────────────────────────────────────────────────────

def is_ollama_running() -> bool:
    """Return True if the Ollama API responds."""
    try:
        resp = httpx.get(f"{OLLAMA_BASE_URL}/api/tags", timeout=3.0)
        return resp.status_code == 200
    except Exception:
        return False


def get_installed_models() -> list[str]:
    """Return list of model names from Ollama /api/tags. Empty list on failure."""
    try:
        resp = httpx.get(f"{OLLAMA_BASE_URL}/api/tags", timeout=5.0)
        resp.raise_for_status()
        return [m["name"] for m in resp.json().get("models", [])]
    except Exception:
        return []


def _get_loaded_models() -> list[str]:
    """Return names of models currently loaded in Ollama (via GET /api/ps)."""
    try:
        resp = httpx.get(f"{OLLAMA_BASE_URL}/api/ps", timeout=5)
        resp.raise_for_status()
        return [m["name"] for m in resp.json().get("models", [])]
    except Exception:
        return []


def _unload_model(model: str) -> None:
    """Unload an Ollama model from memory."""
    try:
        httpx.post(
            f"{OLLAMA_BASE_URL}/api/generate",
            json={"model": model, "keep_alive": 0},
            timeout=10,
        )
    except Exception:
        pass


# ── Startup memory cleanup ────────────────────────────────────────────────────

def get_top_processes(n: int = 6, min_mb: float = 250.0) -> list[dict]:
    """Return top N processes by RAM usage above min_mb MB."""
    procs = []
    for proc in psutil.process_iter(["name", "pid", "memory_info"]):
        try:
            mb = proc.info["memory_info"].rss / 1e6
            if mb >= min_mb:
                procs.append({"name": proc.info["name"], "pid": proc.info["pid"], "mb": mb})
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass
    return sorted(procs, key=lambda x: x["mb"], reverse=True)[:n]


def run_startup_cleanup() -> None:
    """
    Auto-release idle Ollama models to reclaim RAM/VRAM before startup.
    If memory is still low after cleanup, shows the top RAM consumers as a hint.
    Never blocks or kills processes — display only.
    """
    loaded = _get_loaded_models()
    if loaded:
        before_ram  = get_free_ram_gb()
        before_vram = get_free_vram_gb()
        for model in loaded:
            _unload_model(model)
        time.sleep(1.5)   # give Ollama time to release
        after_ram  = get_free_ram_gb()
        after_vram = get_free_vram_gb()

        freed_ram  = max(0.0, after_ram  - before_ram)
        freed_vram = max(0.0, after_vram - before_vram)
        parts = []
        if freed_ram  > 0.1: parts.append(f"{freed_ram:.1f} GB RAM")
        if freed_vram > 0.1: parts.append(f"{freed_vram:.1f} GB VRAM")
        freed_str = " + ".join(parts) if parts else "memory"
        names = ", ".join(loaded)
        console.print(f"[dim]Released: {names}  →  freed {freed_str}[/dim]")

    # Show top memory users when vision VRAM headroom is tight
    free_vram = get_free_vram_gb()
    free_ram  = get_free_ram_gb()
    vision_vram_needed = _MODEL_VRAM_GB.get(VISION_PRIMARY, 7.3)
    if free_vram < vision_vram_needed and free_ram < MIN_FREE_RAM_GB:
        procs = get_top_processes()
        if procs:
            console.print(
                f"\n[yellow]Memory is tight "
                f"({free_vram:.1f} GB VRAM / {free_ram:.1f} GB RAM free). "
                f"Close these apps to free space:[/yellow]"
            )
            for p in procs:
                console.print(f"  [dim]{p['name']:<32}{p['mb']:6.0f} MB[/dim]")
            console.print()


# ── Model suitability ─────────────────────────────────────────────────────────

def check_model_suitability(model: str, free_ram_gb: float, free_vram_gb: float = 0.0) -> dict:
    """
    VRAM-aware suitability check. Priority order:
      1. GPU  — model fits fully in VRAM           → ready (GPU)
      2. CPU+GPU split — VRAM + RAM covers model   → ready (CPU+GPU)
      3. CPU  — no GPU but RAM is sufficient       → ready (CPU)
      4. marginal / unavailable
    """
    ram_needed  = _MODEL_RAM_GB.get(model, 5.0)
    vram_needed = _MODEL_VRAM_GB.get(model, 5.0)

    if free_vram_gb >= vram_needed:
        status, backend = "ready", "GPU"
        reason = f"fits in VRAM ({free_vram_gb:.1f} GB free)"
    elif free_vram_gb >= vram_needed * 0.85:
        status, backend = "marginal", "GPU"
        reason = f"need {vram_needed:.1f} GB VRAM / have {free_vram_gb:.1f} GB — close apps"
    elif free_vram_gb > 0 and (free_vram_gb + free_ram_gb) >= vram_needed:
        status, backend = "ready", "CPU+GPU"
        reason = f"split: {free_vram_gb:.1f} GB VRAM + {free_ram_gb:.1f} GB RAM"
    elif free_ram_gb >= ram_needed:
        status, backend = "ready", "CPU"
        reason = f"CPU only ({free_ram_gb:.1f} GB RAM free)"
    elif free_ram_gb >= ram_needed * 0.85:
        status, backend = "marginal", "CPU"
        reason = f"need {ram_needed:.1f} GB RAM / have {free_ram_gb:.1f} GB"
    else:
        status, backend = "unavailable", ""
        if free_vram_gb > 0:
            reason = f"need {vram_needed:.1f} GB VRAM / have {free_vram_gb:.1f} GB"
        else:
            reason = f"need {ram_needed:.1f} GB RAM / have {free_ram_gb:.1f} GB"

    return {
        "model":             model,
        "status":            status,
        "backend":           backend,
        "reason":            reason,
        "required_vram_gb":  vram_needed,
        "required_ram_gb":   ram_needed,
    }


# ── RAM / VRAM gate ───────────────────────────────────────────────────────────

def ram_gate(min_gb: float = MIN_FREE_RAM_GB) -> bool:
    """
    Warn if neither VRAM nor RAM is sufficient for the vision model.
    Returns True if vision can load, False otherwise.
    Does NOT block execution — the runtime probe in parser_agent decides.
    """
    free_vram = get_free_vram_gb()
    free_ram  = get_free_ram_gb()
    result    = check_model_suitability(VISION_PRIMARY, free_ram, free_vram)
    if result["status"] == "unavailable":
        console.print(
            f"[yellow]Warning: vision model needs {result['required_vram_gb']:.0f} GB VRAM "
            f"or {result['required_ram_gb']:.0f} GB RAM. "
            f"Have {free_vram:.1f} GB VRAM / {free_ram:.1f} GB RAM. "
            f"Parse will use text-only extraction.[/yellow]"
        )
        return False
    return True


# ── Startup screen ────────────────────────────────────────────────────────────

def show_startup_screen() -> None:
    """Print the hardware + model status panel. Safe to call multiple times."""
    free_ram   = get_free_ram_gb()
    total_ram  = get_total_ram_gb()
    gpu_name   = get_gpu_name()
    total_vram = get_total_vram_gb()
    free_vram  = get_free_vram_gb()
    ollama_ok  = is_ollama_running()
    installed  = get_installed_models() if ollama_ok else []

    # ── banner ────────────────────────────────────────────────────────────────
    console.print(Panel.fit(
        "[bold cyan]cloak[/bold cyan]  Content-aware Local Ollama Agentic Knowledge Parser\n"
        "[dim]Local-only · No data leaves your machine[/dim]",
        border_style="cyan",
    ))

    # ── hardware ──────────────────────────────────────────────────────────────
    hw = Table.grid(padding=(0, 2))
    hw.add_column(style="dim")
    hw.add_column()

    gpu_line = f"{gpu_name}"
    if total_vram > 0:
        gpu_line += f"  {total_vram:.0f} GB VRAM"
    if free_vram > 0:
        gpu_line += f"  ({free_vram:.1f} GB free)"

    hw.add_row("GPU", gpu_line)
    hw.add_row("RAM", f"{total_ram:.0f} GB total  /  {free_ram:.1f} GB free")

    ollama_status = "[green]running[/green]" if ollama_ok else "[red]not running[/red]"
    hw.add_row("Ollama", f"{OLLAMA_BASE_URL}  {ollama_status}")

    console.print(hw)
    console.print()

    # ── models ────────────────────────────────────────────────────────────────
    models_tbl = Table(show_header=True, header_style="bold dim", box=None, pad_edge=False)
    models_tbl.add_column("", width=2)
    models_tbl.add_column("Model", style="cyan", min_width=24)
    models_tbl.add_column("Role", style="dim", min_width=16)
    models_tbl.add_column("Status", min_width=18)
    models_tbl.add_column("Notes", style="dim")

    for model in (VISION_PRIMARY, ORCHESTRATOR_MODEL, VISION_FALLBACK):
        result    = check_model_suitability(model, free_ram, free_vram)
        is_pulled = any(model in m for m in installed)
        status    = result["status"]
        backend   = result["backend"]

        if not ollama_ok:
            icon, status_str = "[red]✗[/red]", "[red]Ollama offline[/red]"
        elif not is_pulled:
            icon, status_str = "[yellow]![/yellow]", "[yellow]not pulled[/yellow]"
        elif status == "ready":
            label = f"ready ({backend})" if backend else "ready"
            color = "green" if backend in ("GPU", "CPU+GPU") else "yellow"
            icon, status_str = f"[{color}]✓[/{color}]", f"[{color}]{label}[/{color}]"
        elif status == "marginal":
            label = f"marginal ({backend})" if backend else "marginal"
            icon, status_str = "[yellow]~[/yellow]", f"[yellow]{label}[/yellow]"
        else:
            icon, status_str = "[red]✗[/red]", "[red]unavailable[/red]"

        models_tbl.add_row(
            icon, model, _MODEL_ROLE.get(model, ""), status_str, result["reason"]
        )

    console.print(models_tbl)

    # ── warnings ──────────────────────────────────────────────────────────────
    if not ollama_ok:
        console.print(
            "\n[red]Ollama is not running.[/red] Start it with: [bold]ollama serve[/bold]"
        )
    else:
        vision_result = check_model_suitability(VISION_PRIMARY, free_ram, free_vram)
        if vision_result["status"] == "unavailable":
            console.print(
                f"\n[yellow]Vision unavailable — "
                f"need {vision_result['required_vram_gb']:.0f} GB VRAM "
                f"or {vision_result['required_ram_gb']:.0f} GB RAM. "
                f"Parse will use text-only extraction.[/yellow]"
            )

    console.print()
