"""Shared adapter helpers: text replacement, in-place insertion, safe edits, logging.

Used by all adapters to avoid re-implementing read-modify-write logic.
In dry_run mode, changes are only logged, never written to disk.
"""
from __future__ import annotations

import re
from pathlib import Path


def log(msg: str) -> None:
    print(f"   - {msg}")


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def write(path: Path, content: str, dry_run: bool) -> None:
    if dry_run:
        log(f"[dry-run] would write {path.name}")
        return
    path.write_text(content, encoding="utf-8")
    log(f"updated {path.name}")


def replace_once(path: Path, old: str, new: str, dry_run: bool, *, required: bool = True) -> bool:
    """Replace the first occurrence of `old` with `new` in the file."""
    if not path.exists():
        if required:
            raise FileNotFoundError(f"file not found: {path}")
        log(f"skip (missing file): {path.name}")
        return False
    content = read(path)
    if old not in content:
        if required:
            raise ValueError(f"anchor not found in {path.name}: {old[:60]!r}")
        log(f"skip (anchor not found): {path.name}")
        return False
    if old == new:
        return False
    content = content.replace(old, new, 1)
    write(path, content, dry_run)
    return True


def regex_replace(path: Path, pattern: str, repl: str, dry_run: bool, *, count: int = 1, required: bool = True) -> bool:
    if not path.exists():
        if required:
            raise FileNotFoundError(f"file not found: {path}")
        return False
    content = read(path)
    new_content, n = re.subn(pattern, repl, content, count=count)
    if n == 0:
        if required:
            raise ValueError(f"pattern did not match in {path.name}: {pattern}")
        log(f"skip (pattern not matched): {path.name}")
        return False
    write(path, new_content, dry_run)
    return True


def insert_after(path: Path, anchor: str, snippet: str, dry_run: bool, *, required: bool = True) -> bool:
    """Insert `snippet` right after `anchor` (anchor is kept)."""
    if not path.exists():
        if required:
            raise FileNotFoundError(f"file not found: {path}")
        return False
    content = read(path)
    idx = content.find(anchor)
    if idx == -1:
        if required:
            raise ValueError(f"insertion anchor not found in {path.name}: {anchor[:60]!r}")
        return False
    if snippet.strip() in content:
        log(f"skip (already injected): {path.name}")
        return False
    pos = idx + len(anchor)
    content = content[:pos] + snippet + content[pos:]
    write(path, content, dry_run)
    return True


def ensure_file(path: Path, content: str, dry_run: bool) -> None:
    """Write a brand-new file (used to inject new components/widgets)."""
    if dry_run:
        log(f"[dry-run] would create {path.name}")
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    log(f"created {path.name}")
