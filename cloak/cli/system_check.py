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

# Minimum free RAM each model needs to load (GB)
_MODEL_RAM_GB: dict[str, float] = {
    VISION_PRIMARY:     9.0,   # qwen2.5vl:7b
    ORCHESTRATOR_MODEL: 5.5,   # qwen3:8b
    VISION_FALLBACK:    11.0,  # llama3.2-vision:11b
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


# ── Model suitability ─────────────────────────────────────────────────────────

def check_model_suitability(model: str, free_ram_gb: float) -> dict:
    """
    Returns {"model", "status", "reason", "required_gb"}.
    Status: "ready" | "marginal" | "unavailable"
    """
    required = _MODEL_RAM_GB.get(model, 5.0)
    note = f"need {required:.1f} GB / have {free_ram_gb:.1f} GB"
    if free_ram_gb >= required:
        status, reason = "ready", note
    elif free_ram_gb >= required * 0.85:
        status, reason = "marginal", f"{note} — close apps"
    else:
        status, reason = "unavailable", f"{note} — low RAM"
    return {"model": model, "status": status, "reason": reason, "required_gb": required}


# ── RAM gate ──────────────────────────────────────────────────────────────────

def ram_gate(min_gb: float = MIN_FREE_RAM_GB) -> bool:
    """
    Warn if free RAM is below min_gb. Returns True if sufficient, False if not.
    Does NOT block execution — the runtime probe in parser_agent decides.
    """
    free = get_free_ram_gb()
    if free < min_gb:
        console.print(
            f"[yellow]Warning: only {free:.1f} GB free RAM "
            f"(need {min_gb:.1f} GB for vision model). "
            f"Close browser tabs or heavy apps for best results.[/yellow]"
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
    models_tbl.add_column("Status", min_width=12)
    models_tbl.add_column("Notes", style="dim")

    for model in (VISION_PRIMARY, ORCHESTRATOR_MODEL, VISION_FALLBACK):
        result    = check_model_suitability(model, free_ram)
        is_pulled = any(model in m for m in installed)
        status    = result["status"]

        if not ollama_ok:
            icon, status_str = "[red]✗[/red]", "[red]Ollama offline[/red]"
        elif not is_pulled:
            icon, status_str = "[yellow]![/yellow]", "[yellow]not pulled[/yellow]"
        elif status == "ready":
            icon, status_str = "[green]✓[/green]", "[green]ready[/green]"
        elif status == "marginal":
            icon, status_str = "[yellow]~[/yellow]", "[yellow]marginal[/yellow]"
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
    elif free_ram < MIN_FREE_RAM_GB:
        console.print(
            f"\n[yellow]Low RAM: {free_ram:.1f} GB free "
            f"(vision needs {MIN_FREE_RAM_GB:.1f} GB). "
            f"Close heavy apps if vision fails.[/yellow]"
        )

    console.print()
