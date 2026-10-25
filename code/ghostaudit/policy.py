"""Policy evaluation: deletion_rules.csv -> required treatment for each category, applied to observed state."""
from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path

from .common import prefix_of, PREFIX_CATEGORY

PHASE_OPS = {
    "FINDING": ["REPORT_REMAINING_RECORD", "REPORT_EXPOSED_PII", "REPORT_ACTIVE_DATA_FLOW", "REPORT_RESTORE_RISK"],
    "REMEDIATION": ["DISABLE_DATA_FLOW", "BLOCK_RESTORE", "DELETE_RECORD", "REDACT_RECORD", "ANONYMIZE_RECORD"],
    "VERIFICATION": ["VERIFY_RECORD_ABSENT", "VERIFY_RECORD_PRESENT", "VERIFY_RECORD_REDACTED",
                     "VERIFY_RECORD_ANONYMIZED", "VERIFY_DATA_FLOW_DISABLED", "VERIFY_RESTORE_BLOCKED"],
}
OP_STATE = {
    "REPORT_REMAINING_RECORD": "PRESENT", "REPORT_EXPOSED_PII": "PRESENT", "REPORT_ACTIVE_DATA_FLOW": "ACTIVE",
    "REPORT_RESTORE_RISK": "ACTIVE", "DISABLE_DATA_FLOW": "DISABLED", "BLOCK_RESTORE": "BLOCKED",
    "DELETE_RECORD": "ABSENT", "REDACT_RECORD": "REDACTED", "ANONYMIZE_RECORD": "ANONYMIZED",
    "VERIFY_RECORD_ABSENT": "ABSENT", "VERIFY_RECORD_PRESENT": "PRESENT", "VERIFY_RECORD_REDACTED": "REDACTED",
    "VERIFY_RECORD_ANONYMIZED": "ANONYMIZED", "VERIFY_DATA_FLOW_DISABLED": "DISABLED",
    "VERIFY_RESTORE_BLOCKED": "BLOCKED",
}
OP_PHASE = {op: ph for ph, ops in PHASE_OPS.items() for op in ops}
OP_RANK = {op: i for ph, ops in PHASE_OPS.items() for i, op in enumerate(ops)}
PHASE_RANK = {"FINDING": 0, "REMEDIATION": 1, "VERIFICATION": 2}

# category -> (rule action, remediation op, finding op, verify op, compliant states)
TREATMENT = {
    "DELETE":   ("DELETE_RECORD", "REPORT_REMAINING_RECORD", "VERIFY_RECORD_ABSENT", {"ABSENT"}),
    "REDACT":   ("REDACT_RECORD", "REPORT_EXPOSED_PII", "VERIFY_RECORD_REDACTED", {"REDACTED", "ABSENT"}),
    "ANONYMIZE": ("ANONYMIZE_RECORD", "REPORT_EXPOSED_PII", "VERIFY_RECORD_ANONYMIZED", {"ANONYMIZED", "ABSENT"}),
}


class Rules:
    def __init__(self, dataset_dir: Path):
        self.rows = {}
        with open(Path(dataset_dir) / "deletion_rules.csv", newline="", encoding="utf-8") as fh:
            for r in csv.DictReader(fh):
                self.rows[r["data_category"]] = r

    def action(self, category: str) -> str:
        return self.rows[category]["required_action"] if category in self.rows else "DELETE"


@dataclass
class Assessment:
    rid: str
    system: str
    category: str
    action: str                 # DELETE | REDACT | ANONYMIZE | RETAIN | FLOW | RESTORE
    state: str | None           # observed state at audit_at (None = no eligible evidence)
    violation: bool
    finding_op: str | None
    remediation_op: str | None
    verify_op: str | None
    evidence_id: str            # evidence proving the observed state ("" when none)
    detail: str = ""


def refine_category(rid: str, obs) -> str:
    p = prefix_of(rid)
    cat = PREFIX_CATEGORY.get(p, "USER_PROFILE")
    if p == "support":
        if obs is not None and (obs.attrs.get("attachment")):
            return "SUPPORT_ATTACHMENT"
        if obs is not None and obs.src in ("contact_query_completed", "deletion_marker_check"):
            return "SUPPORT_ATTACHMENT"     # legacy contact/ticket copy owned only by the subject -> delete
        return "SUPPORT_MESSAGE"
    if p == "analytics" and obs is not None and obs.attrs.get("marketing"):
        return "MARKETING_PROFILE"
    return cat


def assess(rules: Rules, rid: str, obs, audit_at) -> Assessment:
    from .common import system_of
    cat = refine_category(rid, obs)
    act = rules.action(cat)
    st = obs.st if obs is not None else None
    ev = obs.file_id if obs is not None else ""
    sysid = system_of(rid)
    if cat == "DATA_FLOW":
        viol = st == "ACTIVE"
        return Assessment(rid, sysid, cat, "FLOW", st, viol, "REPORT_ACTIVE_DATA_FLOW" if viol else None,
                          "DISABLE_DATA_FLOW" if viol else None, "VERIFY_DATA_FLOW_DISABLED", ev)
    if cat == "BACKUP_ARCHIVE":
        viol = st in ("RESTORABLE", "ACTIVE")
        return Assessment(rid, sysid, cat, "RESTORE", st, viol, "REPORT_RESTORE_RISK" if viol else None,
                          "BLOCK_RESTORE" if viol else None, "VERIFY_RESTORE_BLOCKED", ev)
    if act == "RETAIN":
        retained_ok = True
        if obs is not None and obs.retained_until is not None and obs.retained_until < audit_at:
            retained_ok = False
        return Assessment(rid, sysid, cat, "RETAIN", st, False, None, None, "VERIFY_RECORD_PRESENT", ev,
                          "within retention" if retained_ok else "retention elapsed")
    remed, find, ver, ok_states = TREATMENT[act if act in TREATMENT else "DELETE"]
    if act == "DELETE":
        viol = st in ("PRESENT", "IDENTIFIABLE", "REDACTED", "ANONYMIZED", "RESTORABLE", "BLOCKED", "ACTIVE")
    elif act == "REDACT":
        viol = st in ("IDENTIFIABLE", "PRESENT")
    else:  # ANONYMIZE
        viol = st in ("IDENTIFIABLE", "PRESENT")
    return Assessment(rid, sysid, cat, act, st, viol, find if viol else None, remed if viol else None, ver, ev)
