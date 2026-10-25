"""Deterministic audit engine.

Pipeline per case:
  1. classify request intent (NOT_APPLICABLE short-circuit)
  2. resolve current + historical identifiers
  3. link evidence / activity to the subject  (tiered, each link records *why*)
  4. choose the state of every linked resource at ``audit_at``
  5. apply deletion_rules.csv -> findings / remediation / verification
  6. serialise in the exact phase -> operation -> system -> resource order
"""
from __future__ import annotations

import csv
import json
from dataclasses import dataclass, field
from datetime import timedelta
from pathlib import Path

from .common import parse_ts, prefix_of, system_of
from .evidence import EvidenceStore, load_evidence
from .identity import Identity, IdentityResolver
from .intent import classify_request
from .policy import (OP_PHASE, OP_RANK, OP_STATE, PHASE_RANK, Assessment, Rules, assess)
from .timeline import eligible, latest


@dataclass
class Case:
    case_id: str
    email: str
    requested_at: object
    reported_status: str
    reported_at: object
    audit_at: object
    request_text: str


@dataclass
class Link:
    rid: str
    tier: str            # evidence_id | convention | activity_window
    note: str = ""


@dataclass
class CaseResult:
    case: Case
    status: str
    steps: list
    identity: Identity | None
    intent: dict
    links: dict = field(default_factory=dict)
    assessments: list = field(default_factory=list)
    notes: list = field(default_factory=list)


def load_cases(path: Path) -> list[Case]:
    out = []
    with open(path, newline="", encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            out.append(Case(r["case_id"], r["email"], parse_ts(r["requested_at"]), r["reported_status"],
                            parse_ts(r["reported_at"]), parse_ts(r["audit_at"]), r["request_text"]))
    return out


class Activity:
    def __init__(self, dataset_dir: Path):
        self.rows = []
        with open(Path(dataset_dir) / "system_activity.csv", newline="", encoding="utf-8") as fh:
            for r in csv.DictReader(fh):
                r["t"] = parse_ts(r["event_time"])
                self.rows.append(r)
        self.by_rid = {}
        for r in self.rows:
            self.by_rid.setdefault(r["resource_id"], []).append(r)


class AuditEngine:
    def __init__(self, dataset_dir: Path, use_ocr: bool = True, ocr_cache: dict | None = None):
        self.dir = Path(dataset_dir)
        self.rules = Rules(self.dir)
        self.ids = IdentityResolver(self.dir)
        self.store: EvidenceStore = load_evidence(self.dir, use_ocr=use_ocr, ocr_cache=ocr_cache)
        self.activity = Activity(self.dir)
        self._owner = self._index_owners()
        self.extra_links: dict[str, dict] = {}

    # ---------------------------------------------------------------- activity-window linking (batch)
    ACTIVITY_SPECS = [
        # (prefix, event_type, required result, [(min_offset, max_offset) minutes relative to reported_at])
        ("account", "DELETE_RECORD", None, [(-80, -35)]),
        ("analytics", "ANONYMIZE_RECORD", "SUCCESS", [(-31, -26), (-10, -1)]),
    ]

    def prepare(self, cases: list[Case]):
        """Link resources that carry no identifier (accounts, anonymised analytics) through the activity stream.

        A link is created only when it is *unambiguous*: after removing every resource already tied to some
        subject by evidence, exactly one candidate worker event and exactly one still-unlinked case share the
        same ``reported_at`` window.  Ambiguous groups are left unlinked rather than guessed."""
        self.extra_links = {}
        live = [c for c in cases if classify_request(c.request_text)["authorizes_deletion"]]
        ev_links, idents = {}, {}
        for c in live:
            idt = self.ids.resolve(c.email)
            if idt is None:
                continue
            idents[c.case_id] = idt
            ev_links[c.case_id] = self.link_evidence(idt, c)
            self.link_convention(idt, c, ev_links[c.case_id])
        claimed = set(self._owner)
        for l in ev_links.values():
            claimed.update(l)
        groups: dict = {}
        for c in live:
            if c.case_id in ev_links:
                groups.setdefault(c.reported_at, []).append(c)
        for prefix, etype, result, windows in self.ACTIVITY_SPECS:
            for rep, grp in groups.items():
                need = [c for c in grp if not any(prefix_of(r) == prefix for r in ev_links[c.case_id])
                        and (result != "SUCCESS" or c.reported_status == "COMPLETE")]
                if len(need) != 1:
                    continue
                cands = []
                for r in self.activity.rows:
                    if r["event_type"] != etype or r["source_system"] != "deletion_worker":
                        continue
                    if result and r["reported_result"] != result:
                        continue
                    if r["resource_id"] in claimed or prefix_of(r["resource_id"]) != prefix:
                        continue
                    off = (r["t"] - rep).total_seconds() / 60
                    if any(lo <= off <= hi for lo, hi in windows):
                        cands.append(r["resource_id"])
                cands = sorted(set(cands))
                if len(cands) == 1:
                    cid = need[0].case_id
                    self.extra_links.setdefault(cid, {})[cands[0]] = Link(
                        cands[0], "activity_window", f"only unclaimed {etype} within window of reported_at")

    # ---------------------------------------------------------------- linking
    def _index_owners(self):
        """rid -> set(user_id) for every resource carrying an identifier of some user (decoy detection)."""
        owners: dict[str, set] = {}
        email_to = self.ids.email_to_users
        phone_to, hash_to = {}, {}
        for u in self.ids.users.values():
            for p in u.phones:
                phone_to.setdefault(p, set()).add(u.user_id)
            for h in u.hashes:
                hash_to.setdefault(h, set()).add(u.user_id)
        for o in self.store.obs:
            for k, v in o.all_ids():
                us = (email_to.get(v) if k == "email" else phone_to.get(v) if k == "phone"
                      else hash_to.get(v) if k == "hash" else ({v} if v in self.ids.users else None))
                if us:
                    owners.setdefault(o.rid, set()).update(us)
        return owners

    def link_evidence(self, ident: Identity, case: Case) -> dict[str, Link]:
        links: dict[str, Link] = {}
        want = ident.ids()
        for o in self.store.obs:
            if o.t is not None and (o.t < case.requested_at or o.t > case.audit_at):
                continue
            for k, v in o.all_ids():
                if v in want[k]:
                    if o.rid not in links:
                        links[o.rid] = Link(o.rid, "evidence", f"{k} match in {o.file_id}")
                    break
        return links

    def link_convention(self, ident: Identity, case: Case, links: dict[str, Link]):
        """Story accounts use account_<user number zero-padded>; accept only if the activity stream has that resource."""
        num = ident.user_id.split("_")[1]
        rid = f"account_{int(num):03d}"
        if any(prefix_of(r) == "account" for r in links):
            return
        for ev in self.activity.by_rid.get(rid, []):
            if ev["event_type"] == "DELETE_RECORD" and abs((ev["t"] - case.reported_at).total_seconds()) <= 1800:
                links[rid] = Link(rid, "convention", "account id matches user number; worker delete adjacent to reported_at")
                return

    # ---------------------------------------------------------------- state selection
    def state_obs(self, rid: str, case: Case):
        # state: any observation whose *observation time* lies in [requested_at, audit_at] (a bundle exported
        # later may legitimately describe the state at audit_at).  Citation: only files captured <= audit_at.
        cand = [o for o in self.store.by_rid.get(rid, []) if eligible(o, self.store, case.requested_at, case.audit_at)]
        chosen = latest(cand)
        return chosen, cand

    def cite(self, rid: str, chosen, cand, case: Case, violating: bool = False) -> str:
        if chosen is None:
            return ""
        ok = [o for o in cand if self.store.manifest[o.file_id]["captured_at"] <= case.audit_at
              and (o.st == chosen.st or not violating)]
        l = latest(ok)
        return l.file_id if l else ""

    def coverage_evidence(self, system: str, case: Case):
        """Latest enumeration of ``system`` captured inside [requested_at, audit_at] (absence proof)."""
        best = None
        for c in self.store.coverage:
            if c["system"] != system or c["t"] is None:
                continue
            cap = self.store.manifest[c["file_id"]]["captured_at"]
            if cap > case.audit_at or c["t"] > case.audit_at or c["t"] < case.requested_at:
                continue
            key = (c["t"], c["file_id"])
            if best is None or key > best[0]:
                best = (key, c["file_id"])
        return best[1] if best else ""

    # ---------------------------------------------------------------- audit one case
    def audit(self, case: Case, intent: dict | None = None) -> CaseResult:
        intent = intent or classify_request(case.request_text)
        ident = self.ids.resolve(case.email)
        if not intent["authorizes_deletion"]:
            return CaseResult(case, "NOT_APPLICABLE", [], ident, intent, notes=["request does not authorise deletion"])
        if ident is None:
            return CaseResult(case, "INCOMPLETE", [], None, intent, notes=["email not found in identity records"])
        links = self.link_evidence(ident, case)
        self.link_convention(ident, case, links)
        for rid, lk in self.extra_links.get(case.case_id, {}).items():
            links.setdefault(rid, lk)
        assessments: list[Assessment] = []
        for rid in sorted(links):
            obs, cand = self.state_obs(rid, case)
            a = assess(self.rules, rid, obs, case.audit_at)
            a.evidence_id = self.cite(rid, obs, cand, case, a.violation)
            assessments.append(a)
        incomplete = any(a.violation for a in assessments)
        status = "INCOMPLETE" if incomplete else "COMPLETE"
        steps = self.build_steps(case, assessments, status)
        return CaseResult(case, status, steps, ident, intent, links, assessments)

    # ---------------------------------------------------------------- plan
    def build_steps(self, case: Case, assessments: list[Assessment], status: str) -> list[dict]:
        raw = []
        for a in assessments:
            if a.violation:
                raw.append((a.finding_op, a.system, a.rid, a.evidence_id))
                raw.append((a.remediation_op, a.system, a.rid, ""))
            ev = ""
            if status == "COMPLETE":
                ev = a.evidence_id
                if not ev and a.verify_op == "VERIFY_RECORD_ABSENT":
                    ev = self.coverage_evidence(a.system, case)
            raw.append((a.verify_op, a.system, a.rid, ev))
        raw = [r for r in raw if r[0]]
        raw.sort(key=lambda r: (PHASE_RANK[OP_PHASE[r[0]]], OP_RANK[r[0]], r[1], r[2]))
        steps = []
        for i, (op, sysid, rid, ev) in enumerate(raw, 1):
            steps.append({"step_no": i, "phase": OP_PHASE[op], "system_id": sysid, "resource_id": rid,
                          "operation": op, "state": OP_STATE[op], "evidence_id": ev})
        return steps


def steps_to_json(steps: list[dict]) -> str:
    return json.dumps(steps, ensure_ascii=False, separators=(",", ":"))
