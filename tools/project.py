#!/usr/bin/env python3
"""Project paths, phase manifest, and conservative source fingerprints."""

from __future__ import annotations

import hashlib
import json
import os
import re
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parents[1]
IGNORED_DIRS = frozenset({
    "build", ".git", "vendor", "downloads", ".venv", ".tools-venv", "__pycache__",
})
ID_RE = re.compile(r"P(?:0|[1-9][0-9]*)\Z")
GATE_RE = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*\Z")
SHA256_RE = re.compile(r"[0-9a-f]{64}\Z")


def resolve_project_path(root: Path, value: str, *, require_file: bool = False) -> Path:
    """Resolve a repository-relative path without traversal or external symlinks."""
    if not isinstance(value, str) or not value or "\x00" in value or "\\" in value:
        raise ValueError("Expected a nonempty forward-slash repository-relative path")
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts or re.match(r"^[A-Za-z]:", value):
        raise ValueError(f"Path must stay within the project: {value!r}")
    base = root.resolve()
    resolved = (base / path).resolve()
    if not resolved.is_relative_to(base):
        raise ValueError(f"Path escapes the project: {value!r}")
    if require_file and not resolved.is_file():
        raise ValueError(f"Required file does not exist: {value}")
    return resolved


def _strings(value: object, field: str, *, nonempty: bool = False) -> list[str]:
    if not isinstance(value, list) or (nonempty and not value):
        raise ValueError(f"{field} must be a list of nonempty strings")
    if any(not isinstance(item, str) or not item.strip() for item in value):
        raise ValueError(f"{field} must be a list of nonempty strings")
    if len(set(value)) != len(value):
        raise ValueError(f"{field} contains duplicates")
    return value


def load_manifest(root: Path = ROOT) -> dict:
    """Validate local references and require dependency order in phases.json."""
    path = resolve_project_path(root, "config/phases.json", require_file=True)
    if path.stat().st_size > 1024 * 1024:
        raise ValueError("Phase manifest is too large")
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or type(data.get("schema_version")) is not int or data["schema_version"] != 1:
        raise ValueError("Unsupported phase manifest schema")
    resolve_project_path(root, data.get("contract"), require_file=True)
    topics = data.get("topics")
    phases = data.get("phases")
    if not isinstance(topics, dict) or not isinstance(phases, list) or not phases:
        raise ValueError("Manifest requires topics and a nonempty phases list")
    for topic, refs in topics.items():
        if not isinstance(topic, str) or not GATE_RE.fullmatch(topic):
            raise ValueError("Invalid topic name")
        for ref in _strings(refs, f"topics.{topic}", nonempty=True):
            resolve_project_path(root, ref, require_file=True)
    seen_phases: set[str] = set()
    seen_gates: set[str] = set()
    for phase in phases:
        if not isinstance(phase, dict):
            raise ValueError("Each phase must be an object")
        phase_id = phase.get("id")
        if not isinstance(phase_id, str) or not ID_RE.fullmatch(phase_id) or phase_id in seen_phases:
            raise ValueError("Phase IDs must be unique P<number> values")
        if not isinstance(phase.get("title"), str) or not phase["title"].strip():
            raise ValueError(f"{phase_id} requires a title")
        dependencies = _strings(phase.get("depends_on"), f"{phase_id}.depends_on")
        if any(dependency not in seen_phases for dependency in dependencies):
            raise ValueError(f"{phase_id} depends on a later, missing, or cyclic phase")
        gates = _strings(phase.get("gates"), f"{phase_id}.gates", nonempty=True)
        if any(not GATE_RE.fullmatch(gate) or gate in seen_gates for gate in gates):
            raise ValueError("Gate IDs must be unique lowercase hyphenated values")
        selected_topics = _strings(phase.get("topics"), f"{phase_id}.topics")
        if any(topic not in topics for topic in selected_topics):
            raise ValueError(f"{phase_id} references an unknown topic")
        _strings(phase.get("freeze"), f"{phase_id}.freeze", nonempty=True)
        seen_phases.add(phase_id)
        seen_gates.update(gates)
    return data


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def source_fingerprint(root: Path = ROOT) -> str:
    """Hash source paths/content; any source or doc edit invalidates old receipts.

    Build output, dependencies and interpreter caches do not affect the hash.
    Symlink targets are recorded as text, never followed into another directory.
    """
    base = root.resolve()
    entries: list[tuple[str, Path]] = []
    for directory, subdirs, filenames in os.walk(base, followlinks=False):
        parent = Path(directory)
        kept = []
        for name in subdirs:
            path = parent / name
            if name in IGNORED_DIRS:
                continue
            if path.is_symlink():
                entries.append((path.relative_to(base).as_posix(), path))
            else:
                kept.append(name)
        subdirs[:] = sorted(kept)
        for name in filenames:
            if name.endswith((".pyc", ".pyo")):
                continue
            path = parent / name
            entries.append((path.relative_to(base).as_posix(), path))
    digest = hashlib.sha256()
    for relative, path in sorted(entries):
        digest.update(relative.encode("utf-8") + b"\0")
        if path.is_symlink():
            digest.update(b"link\0" + os.readlink(path).encode("utf-8"))
        else:
            digest.update(b"file\0" + bytes.fromhex(file_sha256(path)))
        digest.update(b"\0")
    return digest.hexdigest()
