"""
system_check.py — hardware probe + model readiness display.

Called by the CLI before every command to show the startup screen.
All functions are safe to call anytime — they never raise; failures return
sentinel values (0.0, "unknown", empty list) and display a warning instead.
"""
from __future__ import annotations

import subprocess
import sys
import time

import httpx
import psutil
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

# Reconfigure stdout in-place — does NOT replace the object (avoids closed-file bugs)
if sys.platform == "win32" and hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

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
    VISION_FALLBACK:    4.5,   # qwen3-vl:4b
}

# Approximate VRAM required at Q4_K_M quantization (±0.5 GB)
# Models run on GPU when VRAM is sufficient; Ollama auto-splits to CPU+GPU otherwise
_MODEL_VRAM_GB: dict[str, float] = {
    VISION_PRIMARY:     7.3,   # qwen2.5vl:7b — vision encoder makes it ~7.3 GB
    ORCHESTRATOR_MODEL: 5.2,   # qwen3:8b
    VISION_FALLBACK:    3.5,   # qwen3-vl:4b
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
        note = f"{free_vram_gb:.1f} GB VRAM free"
    elif free_vram_gb >= vram_needed * 0.85:
        status, backend = "marginal", "GPU"
        note = f"need {vram_needed:.1f} GB, have {free_vram_gb:.1f} GB"
    elif free_vram_gb > 0 and (free_vram_gb + free_ram_gb) >= vram_needed:
        status, backend = "ready", "CPU+GPU"
        note = f"{free_vram_gb:.1f} GB VRAM + {free_ram_gb:.1f} GB RAM"
    elif free_ram_gb >= ram_needed:
        status, backend = "ready", "CPU"
        note = f"{free_ram_gb:.1f} GB RAM free"
    elif free_ram_gb >= ram_needed * 0.85:
        status, backend = "marginal", "CPU"
        note = f"need {ram_needed:.1f} GB, have {free_ram_gb:.1f} GB RAM"
    else:
        status, backend = "unavailable", ""
        if free_vram_gb > 0:
            note = f"need {vram_needed:.1f} GB VRAM, have {free_vram_gb:.1f} GB"
        else:
            note = f"need {ram_needed:.1f} GB RAM, have {free_ram_gb:.1f} GB"

    return {
        "model":             model,
        "status":            status,
        "backend":           backend,
        "note":              note,
        "reason":            note,   # backward-compat alias
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

def show_startup_screen(show_commands: bool = False) -> None:
    """Print the hardware + model status panel. Safe to call multiple times."""
    free_ram   = get_free_ram_gb()
    total_ram  = get_total_ram_gb()
    gpu_name   = get_gpu_name()
    total_vram = get_total_vram_gb()
    free_vram  = get_free_vram_gb()
    ollama_ok  = is_ollama_running()
    installed  = get_installed_models() if ollama_ok else []

    # ── banner ────────────────────────────────────────────────────────────────
    _ASCII = (
        "   ___  _    ___   ___  _  __\n"
        "  / __|| |  / _ \\ / __|| |/ /\n"
        " | (__ | |_| (_) | (__ | ' < \n"
        "  \\___||____\\___/ \\___||_|\\_\\\n"
        "  [dim]PDF → Markdown  ·  local-only[/dim]"
    )
    console.print(Panel.fit(_ASCII, border_style="cyan"))

    # ── hardware ──────────────────────────────────────────────────────────────
    hw = Table.grid(padding=(0, 2))
    hw.add_column(style="bold dim", min_width=8)
    hw.add_column()

    gpu_line = gpu_name
    if total_vram > 0:
        gpu_line += f"  [dim]{total_vram:.0f} GB VRAM[/dim]"
    if free_vram > 0:
        gpu_line += f"  [green]{free_vram:.1f} GB free[/green]"

    hw.add_row("GPU", gpu_line)
    hw.add_row("RAM", f"{total_ram:.0f} GB total  [dim]/[/dim]  [green]{free_ram:.1f} GB free[/green]")

    ollama_dot = "[green]●[/green]" if ollama_ok else "[red]●[/red]"
    ollama_txt = "running" if ollama_ok else "[red]not running — start with: ollama serve[/red]"
    hw.add_row("Ollama", f"{ollama_dot}  {OLLAMA_BASE_URL}  {ollama_txt}")

    console.print(hw)
    console.print()

    # ── models ────────────────────────────────────────────────────────────────
    models_tbl = Table(show_header=True, header_style="dim", box=None, pad_edge=False,
                       show_edge=False)
    models_tbl.add_column("",      width=3,        no_wrap=True)
    models_tbl.add_column("Model", style="cyan",   min_width=22, no_wrap=True)
    models_tbl.add_column("Role",  style="dim",    min_width=17, no_wrap=True)
    models_tbl.add_column("Status · Note")

    for model in (VISION_PRIMARY, ORCHESTRATOR_MODEL, VISION_FALLBACK):
        result    = check_model_suitability(model, free_ram, free_vram)
        is_pulled = any(model in m for m in installed)
        status    = result["status"]
        backend   = result["backend"]
        note      = result["note"]

        if not ollama_ok:
            icon       = "[red]✗[/red]"
            status_str = "[red]Ollama offline[/red]"
        elif not is_pulled:
            icon       = "[yellow]![/yellow]"
            status_str = f"[yellow]not pulled[/yellow]  [dim]ollama pull {model}[/dim]"
        elif status == "ready":
            color      = "green" if backend in ("GPU", "CPU+GPU") else "yellow"
            label      = f"ready ({backend})" if backend else "ready"
            icon       = f"[{color}]✓[/{color}]"
            status_str = f"[{color}]{label}[/{color}]  [dim]{note}[/dim]"
        elif status == "marginal":
            label      = f"marginal ({backend})" if backend else "marginal"
            icon       = "[yellow]~[/yellow]"
            status_str = f"[yellow]{label}[/yellow]  [dim]{note}[/dim]"
        else:
            icon       = "[red]✗[/red]"
            status_str = f"[red]unavailable[/red]  [dim]{note}[/dim]"

        models_tbl.add_row(icon, model, _MODEL_ROLE.get(model, ""), status_str)

    console.print(models_tbl)
    console.print()

    # ── warnings ──────────────────────────────────────────────────────────────
    if ollama_ok:
        vision_result = check_model_suitability(VISION_PRIMARY, free_ram, free_vram)
        if vision_result["status"] == "unavailable":
            console.print(
                f"[yellow]Vision unavailable — need {vision_result['required_vram_gb']:.0f} GB "
                f"VRAM or {vision_result['required_ram_gb']:.0f} GB RAM. "
                f"Parse will use text-only extraction.[/yellow]\n"
            )

    # ── commands help (shown only on bare `cloak`) ────────────────────────────
    if show_commands:
        cmds = Table.grid(padding=(0, 2))
        cmds.add_column(style="bold cyan", no_wrap=True)
        cmds.add_column(style="dim")
        cmds.add_row("cloak parse <pdf>", "parse a single PDF")
        cmds.add_row("cloak parse <dir>", "parse all PDFs in a directory")
        cmds.add_row("cloak list",        "list all parsed documents")
        cmds.add_row("cloak status",      "hardware & model status")
        console.print(cmds)
        console.print()
