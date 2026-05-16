"""cloak CLI — installed as the `cloak` command via pyproject.toml."""
from __future__ import annotations

import sys
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

app = typer.Typer(
    name="cloak",
    help="Content-aware Local Ollama Agentic Knowledge Parser",
    add_completion=False,
    no_args_is_help=False,
)
console = Console()


@app.callback(invoke_without_command=True)
def startup(ctx: typer.Context) -> None:
    """Show hardware + model status (default when no subcommand is given)."""
    from cloak.cli import system_check
    system_check.show_startup_screen()
    if ctx.invoked_subcommand is None:
        console.print(
            "Commands: [bold]parse[/bold] <pdf|dir>  "
            "[bold]status[/bold]  "
            "[bold]list[/bold]\n"
        )
        raise typer.Exit()


@app.command()
def parse(
    path: Path = typer.Argument(..., help="PDF file or directory of PDFs to parse"),
) -> None:
    """Parse a PDF file or all PDFs in a directory."""
    from cloak.cli import system_check
    from cloak.orchestration.parser_agent import parse as do_parse

    system_check.show_startup_screen()

    if not path.exists():
        console.print(f"[red]Path not found: {path}[/red]")
        raise typer.Exit(1)

    pdfs: list[Path]
    if path.is_dir():
        pdfs = sorted(path.rglob("*.pdf"))
        if not pdfs:
            console.print(f"[yellow]No PDF files found in {path}[/yellow]")
            raise typer.Exit(1)
        console.print(f"Found [bold]{len(pdfs)}[/bold] PDF(s) in [cyan]{path}[/cyan]\n")
    elif path.suffix.lower() == ".pdf":
        pdfs = [path]
    else:
        console.print(f"[red]{path} is not a PDF file or directory[/red]")
        raise typer.Exit(1)

    errors: list[tuple[Path, str]] = []
    for i, pdf in enumerate(pdfs, 1):
        if len(pdfs) > 1:
            console.rule(f"[dim]{i}/{len(pdfs)}: {pdf.name}[/dim]")
        try:
            do_parse(pdf)
        except Exception as exc:
            console.print(f"[red]Failed: {pdf.name} — {exc}[/red]")
            errors.append((pdf, str(exc)))

    if errors:
        console.print(f"\n[red]{len(errors)} file(s) failed:[/red]")
        for p, msg in errors:
            console.print(f"  [red]✗[/red] {p.name}: {msg}")
        raise typer.Exit(1)


@app.command()
def status() -> None:
    """Show hardware + model status."""
    from cloak.cli import system_check
    system_check.show_startup_screen()


@app.command("list")
def list_docs() -> None:
    """List all parsed documents in data/markdown/."""
    from cloak.config import MD_DIR
    import datetime

    if not MD_DIR.exists():
        console.print(f"[dim]No output directory at {MD_DIR}[/dim]")
        raise typer.Exit()

    md_files = sorted(
        (f for f in MD_DIR.rglob("*.md") if not f.name.endswith("_confidence.md")),
        key=lambda f: f.stat().st_mtime,
        reverse=True,
    )

    if not md_files:
        console.print(f"[dim]No parsed documents in {MD_DIR}[/dim]")
        raise typer.Exit()

    tbl = Table(title=f"Parsed documents  ({MD_DIR})", show_header=True)
    tbl.add_column("File", style="cyan")
    tbl.add_column("Size", justify="right")
    tbl.add_column("Parsed", style="dim")
    tbl.add_column("Confidence", style="dim")

    for f in md_files:
        stat = f.stat()
        size = (
            f"{stat.st_size // 1024} KB" if stat.st_size >= 1024 else f"{stat.st_size} B"
        )
        mtime = datetime.datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M")
        conf = f.with_name(f.stem + "_confidence.md")
        has_conf = "[green]yes[/green]" if conf.exists() else "[dim]—[/dim]"
        tbl.add_row(str(f.relative_to(MD_DIR)), size, mtime, has_conf)

    console.print(tbl)


if __name__ == "__main__":
    app()
