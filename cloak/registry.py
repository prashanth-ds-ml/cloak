"""
registry.py — workspace-local document registry.

Tracks every PDF cloak has seen: parse status, quality scores, processing history.
Registry file lives at {workspace}/.cloak/registry.json.

The workspace is always the directory passed explicitly or cwd at call time.
No init step required — the registry is created on first write.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_REGISTRY_DIR  = ".cloak"
_REGISTRY_FILE = "registry.json"
_VERSION = 1

# ── Status constants ───────────────────────────────────────────────────────────

PENDING    = "pending"
PROCESSING = "processing"
DONE       = "done"
FLAGGED    = "flagged"    # parsed but has low-confidence pages
ERROR      = "error"


# ── Timestamps ────────────────────────────────────────────────────────────────

def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ── I/O ───────────────────────────────────────────────────────────────────────

def _registry_path(workspace: Path) -> Path:
    return workspace / _REGISTRY_DIR / _REGISTRY_FILE


def load(workspace: Path | None = None) -> tuple[dict, Path]:
    """
    Load the registry for workspace (defaults to cwd).
    Creates an empty registry in memory if the file does not exist yet.
    Returns (registry_dict, resolved_workspace_path).
    """
    ws   = (workspace or Path.cwd()).resolve()
    path = _registry_path(ws)
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            data.setdefault("documents", {})
            return data, ws
        except Exception:
            pass  # corrupt file → start fresh
    return {
        "version":   _VERSION,
        "workspace": str(ws),
        "created":   now_iso(),
        "updated":   now_iso(),
        "documents": {},
    }, ws


def save(registry: dict, workspace: Path) -> None:
    """Write registry atomically via a temp file. Never raises — logs and returns."""
    try:
        registry["updated"] = now_iso()
        path = _registry_path(workspace)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(
            json.dumps(registry, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        tmp.replace(path)
    except Exception:
        pass  # registry write failures must never break the parse


# ── Key ───────────────────────────────────────────────────────────────────────

def _key(pdf_path: Path, workspace: Path) -> str:
    """
    Stable registry key: path relative to workspace using forward slashes.
    Falls back to the absolute path if the PDF is outside the workspace.
    """
    try:
        return pdf_path.resolve().relative_to(workspace).as_posix()
    except ValueError:
        return pdf_path.resolve().as_posix()


# ── Entry CRUD ────────────────────────────────────────────────────────────────

def upsert(
    registry: dict,
    pdf_path: Path,
    workspace: Path,
    **fields: Any,
) -> dict:
    """
    Add or update the registry entry for pdf_path.
    Merges fields into the existing entry (or creates a new one).
    Returns the updated entry dict.
    """
    key   = _key(pdf_path, workspace)
    entry = registry["documents"].get(key) or {
        "pdf":   key,
        "added": now_iso(),
    }
    entry.update(fields)
    registry["documents"][key] = entry
    return entry


def get(registry: dict, pdf_path: Path, workspace: Path) -> dict | None:
    """Return the entry for pdf_path, or None if not tracked."""
    return registry["documents"].get(_key(pdf_path, workspace))


def remove(registry: dict, pdf_path: Path, workspace: Path) -> bool:
    """Remove an entry. Returns True if it existed."""
    key = _key(pdf_path, workspace)
    if key in registry["documents"]:
        del registry["documents"][key]
        return True
    return False


# ── Queries ────────────────────────────────────────────────────────────────────

def all_docs(registry: dict) -> list[dict]:
    return list(registry["documents"].values())


def by_status(registry: dict, *statuses: str) -> list[dict]:
    return [d for d in registry["documents"].values() if d.get("status") in statuses]


def pending_docs(registry: dict) -> list[dict]:
    return by_status(registry, PENDING)


def done_docs(registry: dict) -> list[dict]:
    return by_status(registry, DONE, FLAGGED)


def flagged_docs(registry: dict) -> list[dict]:
    """Entries where status=flagged OR flagged_pages > 0 from any run."""
    return [
        d for d in registry["documents"].values()
        if d.get("status") == FLAGGED or d.get("flagged_pages", 0) > 0
    ]


def error_docs(registry: dict) -> list[dict]:
    return by_status(registry, ERROR)


# ── Discovery ──────────────────────────────────────────────────────────────────

def discover(directory: Path, registry: dict, workspace: Path) -> list[Path]:
    """
    Find PDF files in directory that are not yet tracked in the registry.
    Returns sorted list of untracked paths.
    """
    known = set(registry["documents"])
    return [
        p for p in sorted(directory.rglob("*.pdf"))
        if _key(p, workspace) not in known
    ]


def mark_pending(directory: Path, registry: dict, workspace: Path) -> list[Path]:
    """
    Discover untracked PDFs in directory, add them as PENDING, and return the list.
    Does NOT save the registry — caller must call save().
    """
    new_pdfs = discover(directory, registry, workspace)
    for pdf in new_pdfs:
        upsert(registry, pdf, workspace, status=PENDING)
    return new_pdfs
