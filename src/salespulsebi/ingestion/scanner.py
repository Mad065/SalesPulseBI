"""Recursive folder scanning, SHA-256 hashing, and incremental sync diffing."""

from __future__ import annotations

import fnmatch
import hashlib
from dataclasses import dataclass
from pathlib import Path

from salespulsebi.ingestion.readers import SUPPORTED_EXTENSIONS


@dataclass(frozen=True)
class ScannedFile:
    abs_path: Path
    rel_path: str
    folder_label: str
    file_hash: str
    file_size: int
    mtime: float


@dataclass
class SyncDiff:
    added: list[ScannedFile]
    updated: list[ScannedFile]
    removed_paths: list[str]
    unchanged: list[ScannedFile]


def compute_file_hash(path: Path, chunk_size: int = 1024 * 1024) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        while chunk := fh.read(chunk_size):
            h.update(chunk)
    return h.hexdigest()


def _is_ignored_path(path: Path, root: Path) -> bool:
    name = path.name
    if name.startswith(".") or name.startswith("~$"):
        return True
    try:
        rel_parts = path.relative_to(root).parts
    except ValueError:
        rel_parts = path.parts
    return any(part.startswith(".") or part == "__pycache__" for part in rel_parts)


def scan_path(root: Path, recursive: bool = True, file_glob: str = "*") -> list[ScannedFile]:
    """Scan a file or directory for supported tabular sales files."""
    if not root.exists():
        return []
    if root.is_file():
        if root.suffix.lower() not in SUPPORTED_EXTENSIONS or root.name.startswith("~$"):
            return []
        stat = root.stat()
        return [
            ScannedFile(
                abs_path=root.resolve(),
                rel_path=root.name,
                folder_label=root.parent.name or "root",
                file_hash=compute_file_hash(root),
                file_size=stat.st_size,
                mtime=stat.st_mtime,
            )
        ]

    iterator = root.rglob("*") if recursive else root.glob("*")
    files: list[ScannedFile] = []
    for item in sorted(iterator):
        if not item.is_file():
            continue
        if _is_ignored_path(item, root):
            continue
        if item.suffix.lower() not in SUPPORTED_EXTENSIONS:
            continue
        if file_glob and file_glob != "*" and not fnmatch.fnmatch(item.name, file_glob):
            continue
        rel = item.relative_to(root)
        folder_label = str(rel.parent) if str(rel.parent) != "." else root.name
        stat = item.stat()
        files.append(
            ScannedFile(
                abs_path=item.resolve(),
                rel_path=rel.as_posix(),
                folder_label=folder_label,
                file_hash=compute_file_hash(item),
                file_size=stat.st_size,
                mtime=stat.st_mtime,
            )
        )
    return files


def compute_sync_diff(
    previous_hashes: dict[str, str],
    scanned_files: list[ScannedFile],
) -> SyncDiff:
    """Compare previously tracked absolute paths/hashes against currently scanned files."""
    added: list[ScannedFile] = []
    updated: list[ScannedFile] = []
    unchanged: list[ScannedFile] = []
    current_keys: set[str] = set()

    for sf in scanned_files:
        key = str(sf.abs_path)
        current_keys.add(key)
        prev_hash = previous_hashes.get(key)
        if prev_hash is None:
            added.append(sf)
        elif prev_hash != sf.file_hash:
            updated.append(sf)
        else:
            unchanged.append(sf)

    removed_paths = sorted(set(previous_hashes.keys()) - current_keys)
    return SyncDiff(
        added=added,
        updated=updated,
        removed_paths=removed_paths,
        unchanged=unchanged,
    )
