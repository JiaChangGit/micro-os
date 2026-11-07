#!/usr/bin/env python3
"""Print one phase's working context without loading the legacy HTML into chat."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.phase import phase_statuses
from tools.project import ROOT, load_manifest, resolve_project_path

MAX_PACKET_CHARS = 12000


def build_packet(phase_id: str, topic: str | None = None, root: Path = ROOT) -> dict:
    manifest = load_manifest(root)
    phase = next((item for item in manifest["phases"] if item["id"] == phase_id), None)
    if phase is None:
        raise ValueError(f"Unknown phase: {phase_id}")
    if topic is not None and topic not in manifest["topics"]:
        raise ValueError(f"Unknown topic: {topic}")
    selected_topics = [topic] if topic else phase["topics"]
    references = [manifest["contract"], "docs/phases.md"]
    for name in selected_topics:
        references.extend(manifest["topics"][name])
    references = list(dict.fromkeys(references))
    commands = ["python3 tools/preflight.py --profile target", "make check"]
    commands.extend(f"python3 tools/phase.py check {dependency}" for dependency in phase["depends_on"])
    statuses = phase_statuses(root=root, manifest=manifest)
    current = next(item for item in statuses if item["id"] == phase_id)
    packet = {
        "schema_version": 1,
        "phase": phase,
        "status": current,
        "contract_path": manifest["contract"],
        "contract": resolve_project_path(root, manifest["contract"], require_file=True).read_text(encoding="utf-8"),
        "read_next": references,
        "commands": commands,
        "completion_check": f"python3 tools/phase.py check {phase_id}",
        "evidence_note": "Tool self-tests and QEMU transport PASS do not complete a kernel phase. Phase-specific semantic validators are required.",
    }
    if len(json.dumps(packet, ensure_ascii=False, indent=2)) > MAX_PACKET_CHARS:
        packet["contract"] = None
        packet["contract_note"] = f"Contract is too large for this packet. Read {manifest['contract']} explicitly; it was not truncated."
    if len(json.dumps(packet, ensure_ascii=False, indent=2)) > MAX_PACKET_CHARS:
        raise ValueError("Phase context exceeds the packet budget; narrow its manifest and references")
    return packet


def render_packet(packet: dict) -> str:
    phase = packet["phase"]
    current = packet["status"]
    lines = [f"{phase['id']} - {phase['title']} [{current['status']}]", "", "Freeze before implementation:"]
    lines.extend(f"- {item}" for item in phase["freeze"])
    lines.extend(["", "Gates:"])
    lines.extend(f"- {gate['gate_id']}: {gate['status']} ({gate['reason']})" for gate in current["gates"])
    if current["prerequisites"]:
        lines.extend(["", "Prerequisites:"])
        lines.extend(f"- {key}: {value}" for key, value in current["prerequisites"].items())
    lines.extend(["", f"Current contract: {packet['contract_path']}"])
    lines.append(packet["contract"] if packet["contract"] is not None else packet["contract_note"])
    lines.extend(["", "Read only as needed:"])
    lines.extend(f"- {reference}" for reference in packet["read_next"])
    lines.extend(["", "Before implementation (environment, tools and prerequisite gates):"])
    lines.extend(f"  {command}" for command in packet["commands"])
    lines.extend(["", "After implementation (this phase's completion check, not a prerequisite to start):", f"  {packet['completion_check']}"])
    lines.extend(["", packet["evidence_note"]])
    rendered = "\n".join(lines)
    if len(rendered) > MAX_PACKET_CHARS:
        raise ValueError("Rendered context exceeds the packet budget")
    return rendered


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--phase", required=True)
    parser.add_argument("--topic")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    try:
        packet = build_packet(args.phase, args.topic)
        print(json.dumps(packet, ensure_ascii=False, indent=2) if args.json else render_packet(packet))
        return 0
    except (OSError, ValueError, TypeError, UnicodeError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
