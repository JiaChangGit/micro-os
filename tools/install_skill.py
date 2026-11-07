#!/usr/bin/env python3
"""Synchronize the project skill without overwriting unrelated/local edits."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
NAME = "microkernel-engineering"
MANIFEST = ".micro-os-managed.json"


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def safe_file(base: Path, relative: str) -> Path:
    name = Path(relative)
    if name.is_absolute() or not name.parts or any(p in ("..", ".") for p in name.parts):
        raise ValueError("invalid managed path")
    candidate = base / name
    if candidate.is_symlink() or any(p.is_symlink() for p in candidate.parents if p != base.parent):
        raise ValueError("symlinks are not allowed in managed skill paths")
    if not candidate.resolve().is_relative_to(base.resolve()):
        raise ValueError("managed path escapes skill directory")
    return candidate


def source_files(source: Path) -> dict[str, str]:
    if source.is_symlink() or not (source / "SKILL.md").is_file():
        raise ValueError("skill source is missing or is a symlink")
    if not (source / "agents/openai.yaml").is_file():
        raise ValueError("skill UI metadata is missing")
    files = {}
    for path in sorted(source.rglob("*")):
        if path.is_symlink():
            raise ValueError("skill source contains a symlink")
        if path.is_file():
            relative = path.relative_to(source).as_posix()
            if relative == MANIFEST:
                raise ValueError("source must not contain an installation manifest")
            files[relative] = digest(path)
    return files


def atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".skill-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def synchronize(source: Path, target_root: Path, *, check: bool = False) -> dict:
    source = source.resolve()
    files = source_files(source)
    target_root = target_root.expanduser().resolve()
    destination = target_root / NAME
    if destination.is_symlink() or destination.resolve().is_relative_to(source):
        raise ValueError("installation target must be separate from source")
    if destination.exists() and not destination.is_dir():
        raise ValueError("installation target is not a directory")
    old_files: dict[str, str] = {}
    if destination.exists():
        manifest_path = safe_file(destination, MANIFEST)
        if not manifest_path.is_file():
            raise ValueError("existing skill is unmanaged; refusing to overwrite it")
        metadata = json.loads(manifest_path.read_text(encoding="utf-8"))
        if metadata.get("schema_version") != 1 or metadata.get("name") != NAME:
            raise ValueError("invalid managed skill manifest")
        old_files = metadata.get("files")
        if not isinstance(old_files, dict) or not old_files:
            raise ValueError("invalid managed file list")
        # 在任何寫入之前，確認安裝副本沒有本地修改。
        for relative, expected in old_files.items():
            path = safe_file(destination, relative)
            if not path.is_file() or digest(path) != expected:
                raise ValueError(f"installed file was locally modified: {relative}")
        for relative in files.keys() - old_files.keys():
            if safe_file(destination, relative).exists():
                raise ValueError(f"new source conflicts with an unmanaged file: {relative}")
    current = destination.exists() and files == old_files
    if check:
        return {"status": "CURRENT" if current else "OUT_OF_DATE", "path": str(destination), "files": len(files)}
    if not current:
        destination.mkdir(parents=True, exist_ok=True)
        for relative in files:
            atomic_write(safe_file(destination, relative), (source / relative).read_bytes())
        for relative in old_files.keys() - files.keys():
            safe_file(destination, relative).unlink()
        manifest = {"schema_version": 1, "name": NAME, "files": files}
        atomic_write(safe_file(destination, MANIFEST), (json.dumps(manifest, indent=2) + "\n").encode())
    return {"status": "CURRENT" if current else "INSTALLED", "path": str(destination), "files": len(files)}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target", type=Path, default=Path(os.environ.get("CODEX_HOME", str(Path.home() / ".codex"))) / "skills", help="skills root directory")
    parser.add_argument("--check", action="store_true", help="read-only comparison")
    args = parser.parse_args(argv)
    try:
        result = synchronize(ROOT / "skills" / NAME, args.target, check=args.check)
    except (OSError, ValueError, TypeError) as exc:
        print(f"BLOCKED: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result))
    return 1 if result["status"] == "OUT_OF_DATE" else 0


if __name__ == "__main__":
    raise SystemExit(main())
