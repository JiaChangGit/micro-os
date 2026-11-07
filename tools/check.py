#!/usr/bin/env python3
"""Check maintained source/config/document links without building a guest."""
from __future__ import annotations

import ast
import json
from pathlib import Path
import re
import sys
from urllib.parse import unquote, urlsplit

try:
    from .project import ROOT, load_manifest
    from .qemu_command import load_config
except ImportError:
    from project import ROOT, load_manifest
    from qemu_command import load_config


def check(root: Path = ROOT) -> list[str]:
    errors = []
    for folder in ("tools", "tests"):
        for path in sorted((root / folder).rglob("*.py")):
            try:
                ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            except (OSError, SyntaxError, UnicodeError) as exc:
                errors.append(str(exc))
    try:
        load_manifest(root)
        load_config(root / "config/qemu.json")
    except (OSError, ValueError, TypeError, KeyError) as exc:
        errors.append(f"configuration: {exc}")
    # 只驗專案維護的連結，舊 HTML 與第三方原文不納入每日 lint。
    documents = [root / "README.md", root / "AGENTS.md", *(root / "docs").rglob("*.md")]
    for path in documents:
        if not path.is_file():
            errors.append(f"missing document: {path.relative_to(root)}")
            continue
        content = path.read_text(encoding="utf-8")
        for target in re.findall(r"\]\(([^)]+)\)", content):
            target = target.strip().strip("<>")
            parsed = urlsplit(target)
            if parsed.scheme or parsed.netloc or not parsed.path:
                continue
            destination = (path.parent / unquote(parsed.path)).resolve()
            if not destination.is_relative_to(root.resolve()) or not destination.exists():
                errors.append(f"broken local link: {path.relative_to(root)} -> {target}")
    skill = root / "skills/microkernel-engineering/SKILL.md"
    try:
        content = skill.read_text(encoding="utf-8")
        if not re.match(r"\A---\nname: microkernel-engineering\ndescription: .+\n---\n", content):
            errors.append("skill: invalid required frontmatter")
        if "[TODO:" in content:
            errors.append("skill: unfinished scaffold")
    except OSError as exc:
        errors.append(str(exc))
    return errors


def main() -> int:
    try:
        errors = check()
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        errors = [str(exc)]
    for error in errors:
        print(f"FAIL: {error}", file=sys.stderr)
    if not errors:
        print("PASS: source syntax, project config, skill entrypoint and local document links")
    return bool(errors)


if __name__ == "__main__":
    raise SystemExit(main())
