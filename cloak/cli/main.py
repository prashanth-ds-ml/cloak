"""cloak CLI — installed as the `cloak` command via pyproject.toml."""
from __future__ import annotations

import sys
import time
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

# Reconfigure stdout for UTF-8 on Windows (in-place — does not replace the object)
if sys.platform == "win32" and hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

app = typer.Typer(
    name="cloak",
    help="Content-aware Local Ollama Agentic Knowledge Parser",
    add_completion=False,
    no_args_is_help=False,
)
console = Console()


def _format_size(path: Path) -> str:
    b = path.stat().st_size
    return f"{b // 1048576} MB" if b >= 1048576 else f"{b // 1024} KB"


def _format_elapsed(seconds: float) -> str:
    if seconds < 60:
        return f"{seconds:.0f}s"
    m, s = divmod(int(seconds), 60)
    return f"{m}m {s:02d}s"


@app.callback(invoke_without_command=True)
def startup(ctx: typer.Context) -> None:
    """Show hardware + model status (default when no subcommand is given)."""
    if ctx.invoked_subcommand is None:
        from cloak.cli import system_check
        system_check.run_startup_cleanup()
        system_check.show_startup_screen(show_commands=True)
        raise typer.Exit()


@app.command()
def parse(
    path: Path = typer.Argument(..., help="PDF file or directory of PDFs to parse"),
    no_review: bool = typer.Option(False, "--no-review", help="Skip Phase 9 deep quality review"),
    dry_run: bool = typer.Option(False, "--dry-run", help="List PDFs that would be parsed without parsing"),
) -> None:
    """Parse a PDF file or all PDFs in a directory."""
    from cloak.cli import system_check
    from cloak.orchestration.parser_agent import parse as do_parse

    system_check.run_startup_cleanup()

    if not path.exists():
        console.print(f"[red]Path not found: {path}[/red]")
        raise typer.Exit(1)

    pdfs: list[Path]
    if path.is_dir():
        pdfs = sorted(path.rglob("*.pdf"))
        if not pdfs:
            console.print(f"[yellow]No PDF files found in {path}[/yellow]")
            raise typer.Exit(1)

        # Show file list
        console.print(
            f"Found [bold]{len(pdfs)}[/bold] PDF(s) in [cyan]{path}[/cyan]"
        )
        for pdf in pdfs:
            console.print(f"  [dim]·[/dim] {pdf.name:<45} {_format_size(pdf)}")
        console.print()

        if dry_run:
            raise typer.Exit()

    elif path.suffix.lower() == ".pdf":
        pdfs = [path]
        if dry_run:
            console.print(f"  [dim]·[/dim] {path.name}  {_format_size(path)}")
            raise typer.Exit()
    else:
        console.print(f"[red]{path} is not a PDF file or directory[/red]")
        raise typer.Exit(1)

    # Track per-file results for batch summary
    results: list[dict] = []
    errors:  list[tuple[Path, str]] = []
    batch_t0 = time.monotonic()

    for i, pdf in enumerate(pdfs, 1):
        if len(pdfs) > 1:
            console.rule(f"[dim][{i}/{len(pdfs)}] {pdf.name}[/dim]")

        file_t0 = time.monotonic()
        try:
            do_parse(pdf, deep_review=not no_review, workspace=Path.cwd())
            results.append({
                "name":    pdf.stem,
                "elapsed": time.monotonic() - file_t0,
                "status":  "ok",
            })
        except Exception as exc:
            console.print(f"[red]Failed: {pdf.name} — {exc}[/red]")
            errors.append((pdf, str(exc)))
            results.append({
                "name":    pdf.stem,
                "elapsed": time.monotonic() - file_t0,
                "status":  "fail",
            })
            # Mark as ERROR in registry so it shows up correctly in `cloak list`
            try:
                from cloak import registry as _reg_module
                _reg, _ws = _reg_module.load(Path.cwd())
                _reg_module.upsert(_reg, pdf, _ws, status=_reg_module.ERROR)
                _reg_module.save(_reg, _ws)
            except Exception:
                pass

    # Batch summary table (only for multi-file runs)
    if len(pdfs) > 1:
        total_elapsed = time.monotonic() - batch_t0
        console.print()
        tbl = Table(
            title=f"Batch complete — {len(pdfs)} PDF(s)  "
                  f"({len(errors)} failed)  "
                  f"total {_format_elapsed(total_elapsed)}",
            show_header=True,
            header_style="bold dim",
        )
        tbl.add_column("File", style="cyan")
        tbl.add_column("Time", justify="right")
        tbl.add_column("", width=3)

        for r in results:
            icon = "[green]✓[/green]" if r["status"] == "ok" else "[red]✗[/red]"
            tbl.add_row(r["name"], _format_elapsed(r["elapsed"]), icon)

        console.print(tbl)

    if errors:
        raise typer.Exit(1)


@app.command()
def status() -> None:
    """Show hardware + model status."""
    from cloak.cli import system_check
    system_check.run_startup_cleanup()
    system_check.show_startup_screen()


@app.command()
def setup(
    yes: bool = typer.Option(False, "--yes", "-y", help="Pull missing models without prompting"),
) -> None:
    """Detect hardware and configure the best model stack for this machine."""
    from cloak.cli.setup import run_setup
    run_setup(auto_pull=yes)


def _read_best_score(conf_path: Path) -> str:
    """Extract judge score from a confidence report, colour-coded."""
    if not conf_path.exists():
        return "[dim]—[/dim]"
    try:
        import re
        text = conf_path.read_text(encoding="utf-8")
        # New format: Summary table with "| Judge Score | X.X / 10 |"
        m = re.search(r"\|\s*Judge Score\s*\|\s*([\d.]+)\s*/\s*10\s*\|", text)
        if m:
            avg = float(m.group(1))
            color = "green" if avg >= 8.0 else ("yellow" if avg >= 5.0 else "red")
            return f"[{color}]{avg:.1f}[/{color}]"
        # Fallback: old format — parse per-page score cells
        scores = [float(s) for s in re.findall(r"\|\s*\w+\s*\|\s*([\d.]+)\s*\|", text)]
        if not scores:
            return "[dim]—[/dim]"
        avg = sum(scores) / len(scores)
        color = "green" if avg >= 8.0 else ("yellow" if avg >= 5.0 else "red")
        return f"[{color}]{avg:.1f}[/{color}]"
    except Exception:
        return "[dim]?[/dim]"


@app.command("list")
def list_docs() -> None:
    """List all tracked documents (registry + parsed output)."""
    from cloak import registry as _reg_module
    import datetime

    reg, ws = _reg_module.load(Path.cwd())
    docs = _reg_module.all_docs(reg)

    _STATUS_COLOR = {
        _reg_module.DONE:       "green",
        _reg_module.FLAGGED:    "yellow",
        _reg_module.PENDING:    "dim",
        _reg_module.PROCESSING: "cyan",
        _reg_module.ERROR:      "red",
    }
    _STATUS_ICON = {
        _reg_module.DONE:       "✓",
        _reg_module.FLAGGED:    "⚑",
        _reg_module.PENDING:    "·",
        _reg_module.PROCESSING: "⟳",
        _reg_module.ERROR:      "✗",
    }

    if docs:
        # Sort: errors first (need attention), then flagged, then done, then pending
        _ORDER = {
            _reg_module.ERROR:      0,
            _reg_module.FLAGGED:    1,
            _reg_module.DONE:       2,
            _reg_module.PROCESSING: 3,
            _reg_module.PENDING:    4,
        }
        docs.sort(key=lambda d: (
            _ORDER.get(d.get("status", ""), 5),
            d.get("last_parsed", "") or "",
        ), reverse=False)

        tbl = Table(
            title=f"Documents  ·  {ws}",
            show_header=True, header_style="dim",
        )
        tbl.add_column("",       width=2, no_wrap=True)
        tbl.add_column("File",   style="cyan", min_width=28)
        tbl.add_column("Status", min_width=10)
        tbl.add_column("Score",  justify="right")
        tbl.add_column("Pages",  justify="right", style="dim")
        tbl.add_column("Model",  style="dim",     min_width=14, no_wrap=True)
        tbl.add_column("Parsed", style="dim",     min_width=16)

        for d in docs:
            status  = d.get("status", "?")
            color   = _STATUS_COLOR.get(status, "")
            icon    = _STATUS_ICON.get(status, "?")
            icon_s  = f"[{color}]{icon}[/{color}]" if color else icon

            # Score
            js = d.get("judge_score")
            if js is not None:
                sc = "green" if js >= 8.0 else ("yellow" if js >= 5.0 else "red")
                score_s = f"[{sc}]{js:.1f}[/{sc}]"
            elif status == _reg_module.PENDING:
                score_s = "[dim]—[/dim]"
            else:
                score_s = "[dim]—[/dim]"

            # Flagged pages badge
            fp = d.get("flagged_pages", 0)
            name_s = d.get("pdf", "?")
            if fp:
                name_s += f"  [yellow dim]({fp} flagged)[/yellow dim]"

            pages_s  = str(d.get("total_pages", "")) or "[dim]—[/dim]"
            model_s  = d.get("model") or "[dim]—[/dim]"
            lp       = d.get("last_parsed", "")
            parsed_s = lp[:16].replace("T", " ") if lp else "[dim]—[/dim]"

            tbl.add_row(icon_s, name_s, f"[{color}]{status}[/{color}]" if color else status,
                        score_s, pages_s, model_s, parsed_s)

        console.print(tbl)

        # Summary counts
        counts = {}
        for d in docs:
            counts[d.get("status", "?")] = counts.get(d.get("status", "?"), 0) + 1
        parts = [f"[{_STATUS_COLOR.get(s, '')}]{n} {s}[/{_STATUS_COLOR.get(s, '')}]"
                 if _STATUS_COLOR.get(s) else f"{n} {s}"
                 for s, n in sorted(counts.items())]
        console.print(f"  [dim]{'  ·  '.join(parts)}[/dim]\n")

    else:
        console.print("[dim]No documents tracked yet. Run[/dim] cloak parse <pdf> [dim]to get started.[/dim]")


@app.command()
def clean(
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation prompt"),
) -> None:
    """Remove all parsed output from data/markdown/."""
    from cloak.config import MD_DIR
    import shutil

    if not MD_DIR.exists():
        console.print(f"[dim]Nothing to clean — {MD_DIR} does not exist[/dim]")
        raise typer.Exit()

    md_files = list(MD_DIR.rglob("*.md"))
    img_dirs  = [d for d in MD_DIR.rglob("*_images") if d.is_dir()]
    total = len(md_files) + sum(
        len(list(d.rglob("*"))) for d in img_dirs
    )

    if total == 0:
        console.print(f"[dim]Nothing to clean in {MD_DIR}[/dim]")
        raise typer.Exit()

    console.print(
        f"Will delete [bold]{len(md_files)}[/bold] markdown file(s) and "
        f"[bold]{len(img_dirs)}[/bold] image folder(s) in [cyan]{MD_DIR}[/cyan]"
    )

    if not yes:
        confirmed = typer.confirm("Proceed?", default=False)
        if not confirmed:
            console.print("[dim]Aborted.[/dim]")
            raise typer.Exit()

    for f in md_files:
        if f.name != ".gitkeep":
            f.unlink(missing_ok=True)
    for d in img_dirs:
        shutil.rmtree(d, ignore_errors=True)

    console.print(f"[green]✓[/green]  Cleaned {MD_DIR}")


if __name__ == "__main__":
    app()
