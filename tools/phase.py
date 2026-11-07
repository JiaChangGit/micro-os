#!/usr/bin/env python3
"""Derive phase state from semantic evidence, not editable completion flags.

This checks receipt integrity and prerequisite order; it cannot replace each
phase's actual validator. All source/doc edits conservatively stale receipts.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timedelta
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.project import (
    ROOT, SHA256_RE, file_sha256, load_manifest, resolve_project_path,
    source_fingerprint,
)

MAX_RECEIPT_BYTES = 1024 * 1024
DEFAULT_REPORTS = "build/reports/gates"


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def validate_receipt(path: Path, gate: str, phase_id: str, fingerprint: str, root: Path) -> dict:
    """Validate a receipt and every referenced raw evidence digest."""
    result = {"gate_id": gate, "status": "NOT_RUN", "reason": "Receipt is absent"}
    if not path.exists():
        return result
    try:
        _require(path.is_file(), "Receipt is not a regular file")
        _require(path.stat().st_size < MAX_RECEIPT_BYTES, "Receipt exceeds size limit")
        data = json.loads(path.read_text(encoding="utf-8"))
        _require(isinstance(data, dict), "Receipt must be an object")
        _require(type(data.get("schema_version")) is int and data["schema_version"] == 1, "Unsupported receipt schema")
        _require(data.get("gate_id") == gate and data.get("phase") == phase_id, "Receipt gate/phase does not match")
        _require(data.get("evidence_kind") == "phase_gate", "Transport or other evidence is not a phase gate")
        _require(data.get("semantic_validation") is True, "Semantic validation is required")
        _require(data.get("status") == "PASS", "Validator did not report PASS")
        _require(type(data.get("exit_code")) is int and data["exit_code"] == 0, "Validator did not exit zero")
        command = data.get("command")
        _require(isinstance(command, list) and bool(command) and all(isinstance(part, str) and bool(part.strip()) and "\x00" not in part for part in command), "Actual command must be a nonempty string list")
        timestamp = data.get("timestamp_utc")
        _require(isinstance(timestamp, str), "timestamp_utc is required")
        parsed = datetime.fromisoformat(timestamp.removesuffix("Z") + "+00:00" if timestamp.endswith("Z") else timestamp)
        _require(parsed.tzinfo is not None and parsed.utcoffset() == timedelta(0), "timestamp_utc must have UTC timezone")
        assertions = data.get("assertions")
        _require(isinstance(assertions, list) and bool(assertions), "Nonempty semantic assertions are required")
        names: set[str] = set()
        for assertion in assertions:
            _require(isinstance(assertion, dict), "Assertion must be an object")
            name = assertion.get("name")
            _require(isinstance(name, str) and bool(name.strip()) and name not in names, "Assertion names must be nonempty and unique")
            _require(assertion.get("passed") is True, f"Assertion failed: {name}")
            names.add(name)
        expected_fingerprint = data.get("source_fingerprint")
        _require(isinstance(expected_fingerprint, str) and bool(SHA256_RE.fullmatch(expected_fingerprint)), "Invalid source fingerprint")
        evidence = data.get("evidence")
        _require(isinstance(evidence, list) and bool(evidence), "Raw evidence is required")
        for item in evidence:
            _require(isinstance(item, dict), "Evidence must be an object")
            raw_path = resolve_project_path(root, item.get("path"), require_file=True)
            expected_hash = item.get("sha256")
            _require(isinstance(expected_hash, str) and bool(SHA256_RE.fullmatch(expected_hash)), "Invalid evidence digest")
            _require(file_sha256(raw_path) == expected_hash, "Raw evidence hash does not match")
        if expected_fingerprint != fingerprint:
            return {"gate_id": gate, "status": "STALE", "reason": "Source fingerprint changed; rerun the validator"}
        return {"gate_id": gate, "status": "PASS", "reason": "Semantic receipt and evidence verified"}
    except (OSError, ValueError, TypeError, UnicodeError) as error:
        result.update(status="INVALID", reason=str(error))
        return result


def phase_statuses(root: Path = ROOT, reports: str | None = None, manifest: dict | None = None) -> list[dict]:
    manifest = load_manifest(root) if manifest is None else manifest
    resolve_project_path(root, reports or DEFAULT_REPORTS)
    fingerprint = source_fingerprint(root)
    phases: list[dict] = []
    previous: dict[str, dict] = {}
    for phase in manifest["phases"]:
        gates = []
        for gate in phase["gates"]:
            receipt = resolve_project_path(root, f"{reports or DEFAULT_REPORTS}/{gate}.json")
            gates.append(validate_receipt(receipt, gate, phase["id"], fingerprint, root))
        prerequisite_statuses = {dependency: previous[dependency]["status"] for dependency in phase["depends_on"]}
        prerequisites_satisfied = all(status == "PASS" for status in prerequisite_statuses.values())
        if any(gate["status"] == "INVALID" for gate in gates):
            status = "INVALID"
        elif any(gate["status"] == "STALE" for gate in gates):
            status = "STALE"
        elif all(gate["status"] == "PASS" for gate in gates):
            status = "PASS" if prerequisites_satisfied else "BLOCKED"
        else:
            status = "NOT_RUN"
        record = {
            "id": phase["id"], "title": phase["title"], "status": status,
            "depends_on": phase["depends_on"], "prerequisites": prerequisite_statuses,
            "prerequisites_satisfied": prerequisites_satisfied, "gates": gates,
        }
        phases.append(record)
        previous[phase["id"]] = record
    return phases


def _print_status(records: list[dict]) -> None:
    for phase in records:
        print(f"{phase['id']}: {phase['status']} - {phase['title']}")
        for gate in phase["gates"]:
            print(f"  {gate['gate_id']}: {gate['status']} ({gate['reason']})")
        if not phase["prerequisites_satisfied"]:
            print("  Prerequisites: " + ", ".join(f"{key}={value}" for key, value in phase["prerequisites"].items()))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="action", required=True)
    status = commands.add_parser("status", help="Read phase state without writing receipts")
    check = commands.add_parser("check", help="Require a phase and all prerequisites to pass")
    check.add_argument("phase")
    for command in (status, check):
        command.add_argument("--reports", default=DEFAULT_REPORTS, help="Repository-relative receipt directory")
        command.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    try:
        records = phase_statuses(reports=args.reports)
        if args.action == "check":
            selected = next((record for record in records if record["id"] == args.phase), None)
            if selected is None:
                raise ValueError(f"Unknown phase: {args.phase}")
            shown = [selected]
            result = 0 if selected["status"] == "PASS" else 1
        else:
            shown, result = records, 0
        if args.json:
            print(json.dumps({"schema_version": 1, "phases": shown}, ensure_ascii=False, indent=2))
        else:
            _print_status(shown)
        return result
    except (OSError, ValueError, TypeError, UnicodeError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
