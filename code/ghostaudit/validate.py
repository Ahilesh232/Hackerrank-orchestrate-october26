"""Strict submission validator (schema, ordering, evidence rules)."""
from __future__ import annotations

import csv
import json
from pathlib import Path

from .common import parse_ts
from .policy import OP_PHASE, OP_RANK, OP_STATE, PHASE_RANK


def validate_output(output_csv: Path, dataset_dir: Path, expected_rows: int | None = None) -> list[str]:
    errs: list[str] = []
    dataset_dir = Path(dataset_dir)
    reqs = {r["case_id"]: r for r in csv.DictReader(open(dataset_dir / "deletion_requests.csv", encoding="utf-8"))}
    man = {r["file_id"]: parse_ts(r["captured_at"]) for r in csv.DictReader(open(dataset_dir / "evidence_files.csv"))}
    act = {r["activity_id"] for r in csv.DictReader(open(dataset_dir / "system_activity.csv"))}
    aud = {r["audit_id"] for r in csv.DictReader(open(dataset_dir / "user_audit_log.csv"))}
    with open(output_csv, newline="", encoding="utf-8") as fh:
        rd = csv.reader(fh)
        header = next(rd)
        if header != ["case_id", "deletion_status", "steps_json"]:
            errs.append(f"bad header {header}")
        rows = list(rd)
    n = expected_rows if expected_rows is not None else len(reqs)
    if len(rows) != n:
        errs.append(f"expected {n} rows, found {len(rows)}")
    ids = [r[0] for r in rows]
    if ids != sorted(ids):
        errs.append("rows not sorted by case_id")
    if len(set(ids)) != len(ids):
        errs.append("duplicate case_ids")
    if set(ids) != set(reqs):
        errs.append("case_id set differs from deletion_requests.csv")
    for cid, status, sj in rows:
        if status not in ("COMPLETE", "INCOMPLETE", "NOT_APPLICABLE"):
            errs.append(f"{cid}: bad status {status}")
        try:
            steps = json.loads(sj)
        except Exception as e:
            errs.append(f"{cid}: invalid json {e}")
            continue
        if status == "NOT_APPLICABLE" and steps:
            errs.append(f"{cid}: NOT_APPLICABLE must be []")
        audit_at = parse_ts(reqs[cid]["audit_at"]) if cid in reqs else None
        prev = None
        for i, s in enumerate(steps, 1):
            if set(s) != {"step_no", "phase", "system_id", "resource_id", "operation", "state", "evidence_id"}:
                errs.append(f"{cid}: step {i} has wrong keys")
                continue
            if s["step_no"] != i:
                errs.append(f"{cid}: step_no {s['step_no']} != {i}")
            if s["operation"] not in OP_PHASE or OP_PHASE[s["operation"]] != s["phase"] or OP_STATE[s["operation"]] != s["state"]:
                errs.append(f"{cid}: step {i} illegal phase/op/state")
                continue
            key = (PHASE_RANK[s["phase"]], OP_RANK[s["operation"]], s["system_id"], s["resource_id"])
            if prev and key < prev:
                errs.append(f"{cid}: step {i} out of order")
            prev = key
            ev = s["evidence_id"]
            if ev:
                if ev in man:
                    if audit_at and man[ev] > audit_at:
                        errs.append(f"{cid}: step {i} cites {ev} captured after audit_at")
                elif ev not in act and ev not in aud:
                    errs.append(f"{cid}: step {i} cites unknown id {ev}")
            if s["phase"] == "REMEDIATION" and ev:
                errs.append(f"{cid}: remediation step must have empty evidence_id")
            if status == "INCOMPLETE" and s["phase"] == "VERIFICATION" and ev:
                errs.append(f"{cid}: INCOMPLETE verification must have empty evidence_id")
        if status == "COMPLETE" and any(s["phase"] != "VERIFICATION" for s in steps):
            errs.append(f"{cid}: COMPLETE may only contain VERIFICATION steps")
        if status == "INCOMPLETE" and steps and not any(s["phase"] == "FINDING" for s in steps):
            errs.append(f"{cid}: INCOMPLETE without findings")
    return errs
