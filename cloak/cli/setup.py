"""
setup.py — hardware-aware model selection for cloak.

Detects the user's GPU + RAM, picks the best model stack from a curated
catalog, checks what's already installed, and optionally pulls missing models.

Writes the selected stack to .cloak_local.json at the project root.
config.py reads this file at import time to override the defaults.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import httpx
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

console = Console(legacy_windows=False)

# ── Model catalog ─────────────────────────────────────────────────────────────
# Each role lists options best-first. select_stack() picks the first option
# whose min_total_gb fits the machine's VRAM + RAM.

MODEL_CATALOG: dict[str, list[dict]] = {
    "orchestrator": [
        {
            "tag":         "qwen3:14b",
            "size_gb":     9.0,
            "min_total_gb": 12.0,   # ~8 GB VRAM + ~1 GB RAM (D49)
            "desc":        "best — 14B dense, strong FORMAT/PATCH, mostly GPU",
        },
        {
            "tag":         "qwen3.6:27b",
            "size_gb":     17.0,
            "min_total_gb": 24.0,   # needs 8 GB VRAM + 16 GB RAM at minimum
            "desc":        "high-end — 256K ctx, agentic optimised, CPU+GPU split",
        },
        {
            "tag":         "qwen3:8b",
            "size_gb":     5.2,
            "min_total_gb": 7.0,
            "desc":        "min spec — fits 6 GB VRAM, fast",
        },
    ],
    "vision_primary": [
        {
            "tag":         "qwen3-vl:8b",
            "size_gb":     6.1,
            "min_total_gb": 8.0,    # tiny CPU split on 6 GB VRAM machines
            "desc":        "best — next-gen VLM, better OCR, 256K ctx",
        },
        {
            "tag":         "qwen2.5vl:7b",
            "size_gb":     6.0,
            "min_total_gb": 7.0,
            "desc":        "min spec — proven, fits 6 GB VRAM",
        },
    ],
    "vision_fallback": [
        {
            "tag":         "qwen3-vl:4b",
            "size_gb":     3.3,
            "min_total_gb": 4.0,
            "desc":        "lightweight fallback — always GPU",
        },
    ],
    "deep_review": [
        {
            "tag":         "qwen3:14b",
            "size_gb":     9.0,
            "min_total_gb": 12.0,   # reuses orchestrator model — no extra load (D49)
            "desc":        "best — same as orchestrator, reused from Phase 6 (zero extra cost)",
        },
        {
            "tag":         "qwen3:8b",
            "size_gb":     5.2,
            "min_total_gb": 7.0,
            "desc":        "min spec — fits 6 GB VRAM",
        },
    ],
}

# ── Config key map: role → config.py constant name ───────────────────────────
_ROLE_CONFIG_KEY = {
    "orchestrator":   "ORCHESTRATOR_MODEL",
    "vision_primary": "VISION_PRIMARY",
    "vision_fallback": "VISION_FALLBACK",
    "deep_review":    "DEEP_REVIEW_MODEL",
}

_ROLE_LABEL = {
    "orchestrator":   "orchestrator",
    "vision_primary": "vision primary",
    "vision_fallback": "vision fallback",
    "deep_review":    "deep review (Phase 9)",
}

_LOCAL_CONFIG = Path(__file__).parent.parent.parent / ".cloak_local.json"


# ── Hardware helpers ──────────────────────────────────────────────────────────

def _total_vram_gb() -> float:
    try:
        r = subprocess.run(
            ["nvidia-smi", "--query-gpu=memory.total", "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=5,
        )
        if r.returncode == 0:
            return float(r.stdout.strip().splitlines()[0]) / 1024
    except Exception:
        pass
    return 0.0


def _total_ram_gb() -> float:
    try:
        import psutil
        return psutil.virtual_memory().total / 1e9
    except Exception:
        return 0.0


def _gpu_name() -> str:
    try:
        r = subprocess.run(
            ["nvidia-smi", "--query-gpu=name", "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=5,
        )
        if r.returncode == 0:
            return r.stdout.strip().splitlines()[0]
    except Exception:
        pass
    return "unknown"


def _installed_models() -> list[str]:
    from cloak.config import OLLAMA_BASE_URL
    try:
        resp = httpx.get(f"{OLLAMA_BASE_URL}/api/tags", timeout=5.0)
        resp.raise_for_status()
        return [m["name"] for m in resp.json().get("models", [])]
    except Exception:
        return []


def _is_installed(tag: str, installed: list[str]) -> bool:
    return any(tag in m or m in tag for m in installed)


# ── Stack selection ───────────────────────────────────────────────────────────

def select_stack(total_vram_gb: float, total_ram_gb: float) -> dict[str, dict]:
    """
    Pick the best model for each role that fits the hardware.
    Returns a dict mapping role → catalog entry.
    """
    total_gb = total_vram_gb + total_ram_gb
    stack: dict[str, dict] = {}
    for role, options in MODEL_CATALOG.items():
        chosen = options[-1]  # fallback = last (min spec)
        for opt in options:
            if total_gb >= opt["min_total_gb"]:
                chosen = opt
                break
        stack[role] = chosen
    return stack


def _tier_label(total_gb: float) -> str:
    if total_gb >= 30:
        return "High"
    if total_gb >= 24:
        return "Mid"
    return "Min spec"


# ── Pull ──────────────────────────────────────────────────────────────────────

def _pull_model(tag: str) -> bool:
    """Stream an ollama pull and return True on success."""
    console.print(f"  [dim]Pulling[/dim] [cyan]{tag}[/cyan] ...", end=" ")
    try:
        r = subprocess.run(["ollama", "pull", tag], capture_output=False, timeout=3600)
        if r.returncode == 0:
            console.print("[green]✓[/green]")
            return True
        console.print("[red]failed[/red]")
        return False
    except subprocess.TimeoutExpired:
        console.print("[red]timeout[/red]")
        return False
    except FileNotFoundError:
        console.print("[red]ollama not found in PATH[/red]")
        return False


# ── Config write ──────────────────────────────────────────────────────────────

def _write_local_config(stack: dict[str, dict]) -> None:
    cfg: dict[str, str] = {}
    for role, opt in stack.items():
        key = _ROLE_CONFIG_KEY.get(role)
        if key:
            cfg[key] = opt["tag"]
    _LOCAL_CONFIG.write_text(json.dumps(cfg, indent=2), encoding="utf-8")


def _read_local_config() -> dict[str, str]:
    if _LOCAL_CONFIG.exists():
        try:
            return json.loads(_LOCAL_CONFIG.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}


# ── Main setup flow ───────────────────────────────────────────────────────────

def run_setup(auto_pull: bool = False) -> None:
    """
    Detect hardware → recommend model stack → check installed → pull missing.
    Writes .cloak_local.json with the selected model names.
    """
    # ── hardware probe ────────────────────────────────────────────────────────
    console.print()
    console.print("[bold]Detecting hardware...[/bold]")
    vram_gb  = _total_vram_gb()
    ram_gb   = _total_ram_gb()
    gpu_name = _gpu_name()
    total_gb = vram_gb + ram_gb
    tier     = _tier_label(total_gb)

    hw = Table.grid(padding=(0, 2))
    hw.add_column(style="dim", min_width=6)
    hw.add_column()
    hw.add_row("GPU",  f"{gpu_name}  [dim]{vram_gb:.0f} GB VRAM[/dim]")
    hw.add_row("RAM",  f"{ram_gb:.0f} GB")
    hw.add_row("Pool", f"[bold]{total_gb:.0f} GB total[/bold]  →  [cyan]{tier}[/cyan]")
    console.print(hw)
    console.print()

    # ── select best stack ─────────────────────────────────────────────────────
    stack    = select_stack(vram_gb, ram_gb)
    installed = _installed_models()

    tbl = Table(
        title="Recommended model stack",
        show_header=True, header_style="dim",
        box=None, pad_edge=True, show_edge=False,
    )
    tbl.add_column("",            width=3,  no_wrap=True)
    tbl.add_column("Role",        style="dim",  min_width=22, no_wrap=True)
    tbl.add_column("Model",       style="cyan", min_width=18, no_wrap=True)
    tbl.add_column("Size",        justify="right", min_width=6)
    tbl.add_column("Status / Description")

    missing: list[dict] = []
    for role, opt in stack.items():
        tag   = opt["tag"]
        pulled = _is_installed(tag, installed)
        if pulled:
            icon       = "[green]✓[/green]"
            status_str = f"[green]installed[/green]  [dim]{opt['desc']}[/dim]"
        else:
            icon       = "[yellow]✗[/yellow]"
            status_str = f"[yellow]not installed[/yellow]  [dim]{opt['desc']}[/dim]"
            missing.append(opt)
        tbl.add_row(
            icon,
            _ROLE_LABEL.get(role, role),
            tag,
            f"{opt['size_gb']:.0f} GB",
            status_str,
        )

    console.print(tbl)
    console.print()

    # ── summary ───────────────────────────────────────────────────────────────
    already = len(stack) - len(missing)
    console.print(
        f"  [green]{already}[/green] model(s) already installed.  "
        f"[yellow]{len(missing)}[/yellow] need to be pulled."
    )

    if missing:
        total_dl = sum(m["size_gb"] for m in missing)
        tags_str = ", ".join(m["tag"] for m in missing)
        console.print(
            f"  Download: [bold]{tags_str}[/bold]  "
            f"([dim]{total_dl:.0f} GB[/dim])"
        )
        console.print()

        if not auto_pull:
            import typer
            confirmed = typer.confirm("Pull missing models now?", default=True)
            if not confirmed:
                console.print("[dim]Skipped. Run[/dim] cloak setup [dim]again to pull later.[/dim]")
                _write_local_config(stack)
                _show_config_applied(stack)
                return
        else:
            console.print()

        all_ok = True
        for opt in missing:
            ok = _pull_model(opt["tag"])
            if not ok:
                all_ok = False

        if not all_ok:
            console.print("[yellow]Some models failed to pull. Run[/yellow] cloak setup [yellow]again to retry.[/yellow]")
    else:
        console.print("  [green]All recommended models are already installed.[/green]")
        console.print()

    # ── write local config ────────────────────────────────────────────────────
    _write_local_config(stack)
    _show_config_applied(stack)


def _show_config_applied(stack: dict[str, dict]) -> None:
    console.print()
    console.print("[bold green]✓[/bold green]  Stack configured for this machine.")

    cfg = Table.grid(padding=(0, 2))
    cfg.add_column(style="dim", min_width=20)
    cfg.add_column(style="cyan")
    for role, opt in stack.items():
        key = _ROLE_CONFIG_KEY.get(role, "")
        cfg.add_row(key, opt["tag"])
    console.print(cfg)
    console.print()
    console.print(
        f"  [dim]Saved to[/dim] [cyan].cloak_local.json[/cyan]  "
        f"[dim]— overrides config.py defaults for this machine.[/dim]"
    )
    console.print("  Run: [bold cyan]cloak parse <pdf>[/bold cyan]")
    console.print()
