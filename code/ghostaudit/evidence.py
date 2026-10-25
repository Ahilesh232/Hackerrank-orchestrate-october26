"""Evidence ingestion.

Every artefact under ``dataset/evidence`` is converted into a flat list of
:class:`Obs` objects ("at time *t*, file *f* shows resource *r* in state *s*
and mentioning identifiers *ids*").  All parsing is deterministic; the optional
LLM layer (see ``llm.py``) is only used for images/PDFs whose OCR/text layer is
too noisy to be linked by the deterministic matcher.

State vocabulary (``Obs.st``)
    PRESENT       record exists (account, search doc, export, attachment, audit row ...)
    ABSENT        lookup proved the record does not exist (404 / NOT_FOUND / 0 rows)
    IDENTIFIABLE  record exists and still carries direct identifiers
    REDACTED      record exists, direct identifiers removed (support message, tombstone)
    ANONYMIZED    analytics record exists with no direct identifiers
    ACTIVE        data flow enabled
    DISABLED      data flow disabled
    RESTORABLE    backup can still restore
    BLOCKED       backup restore blocked
"""
from __future__ import annotations

import csv
import json
import re
import sqlite3
import subprocess
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from .common import (EMAIL_RE, HASH_RE, PHONE_RE, RID_RE, USER_RE, classify_identifier,
                     norm_email, norm_phone, parse_ts, prefix_of, system_of)

IDENT_FIELDS_IGNORED = {"resource_id", "ticket_id", "file_name", "archive_path", "object_key", "archive_key",
                        "order_reference", "index_name", "connector_name", "destination"}


@dataclass
class Obs:
    file_id: str
    t: datetime | None
    rid: str
    st: str
    origin: str = ""
    ids: dict = field(default_factory=lambda: {"email": set(), "phone": set(), "hash": set(), "user": set()})
    attrs: dict = field(default_factory=dict)
    retained_until: datetime | None = None
    src: str = ""

    def all_ids(self):
        for k, vs in self.ids.items():
            for v in vs:
                yield k, v

    @property
    def system(self) -> str:
        return system_of(self.rid)


def _new_ids():
    return {"email": set(), "phone": set(), "hash": set(), "user": set()}


def _add(ids: dict, val):
    c = classify_identifier(val)
    if c:
        ids[c[0]].add(c[1])


def _collect_ids(obj, ids: dict, skip=IDENT_FIELDS_IGNORED):
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k in skip:
                continue
            _collect_ids(v, ids, skip)
    elif isinstance(obj, list):
        for v in obj:
            _collect_ids(v, ids, skip)
    elif isinstance(obj, str):
        _add(ids, obj)
        for e in EMAIL_RE.findall(obj):
            ids["email"].add(norm_email(e))


def _has_direct(ids: dict) -> bool:
    return bool(ids["email"] or ids["phone"] or ids["hash"])


def _tz(s):
    try:
        return parse_ts(s) if s else None
    except Exception:
        return None


# ----------------------------------------------------------------- state mapping
def _state_for(rid: str, rec: dict, ids: dict, *, collection: str = "") -> tuple[str, dict]:
    """Map a record (dict of fields) to (state, attrs) using its resource prefix."""
    p = prefix_of(rid)
    attrs: dict = {}
    if p == "account":
        status = str(rec.get("account_status") or "").upper()
        attrs["account_status"] = status
        return "PRESENT", attrs
    if p == "search":
        return "PRESENT", attrs
    if p == "export":
        avail = rec.get("download_available", rec.get("available"))
        if avail is False or str(avail).lower() == "false":
            return "ABSENT", attrs
        return "PRESENT", attrs
    if p == "analytics":
        ref = rec.get("customer_reference") or rec.get("user_id")
        ident = _has_direct(ids) or bool(ref)
        return ("IDENTIFIABLE" if ident else "ANONYMIZED"), attrs
    if p in ("support", "object"):
        txt = str(rec.get("message_text") or rec.get("message_excerpt") or "")
        redacted_txt = "[REDACTED]" in txt or not txt
        ref = rec.get("customer_reference")
        if _has_direct(ids) or (ref and not redacted_txt):
            return "IDENTIFIABLE", attrs
        if "file_name" in rec or "uploaded_by" in rec or "storage_class" in rec:
            attrs["attachment"] = True
            return "PRESENT", attrs
        return "REDACTED", attrs
    if p == "data_flow":
        en = rec.get("enabled")
        if isinstance(en, str):
            en = en.lower() in ("true", "1", "yes")
        return ("ACTIVE" if en else "DISABLED"), attrs
    if p == "backup":
        ra = rec.get("restore_allowed")
        if isinstance(ra, str):
            ra = ra.lower() in ("true", "1", "yes")
        return ("RESTORABLE" if ra else "BLOCKED"), attrs
    if p == "audit_record":
        return "PRESENT", attrs
    if p == "tombstone":
        return ("IDENTIFIABLE" if _has_direct(ids) else "REDACTED"), attrs
    if p == "legal_hold":
        return "PRESENT", attrs
    return "PRESENT", attrs


# ----------------------------------------------------------------- SQL
CAP_RE = re.compile(r"Captured at:\s*(\S+)")
TYPED_TABLES = {
    # table-prefix -> (column mapping for "resource_id" is always resource_id)
    "customer_profiles": "account", "customer_search_documents": "search",
    "customer_analytics_profiles": "analytics", "support_records": "support",
    "customer_exports": "export", "connector_subscriptions": "data_flow",
    "backup_restore_entries": "backup", "security_audit_records": "audit_record",
    "search_documents": "search",
}


def parse_sql(fid: str, path: Path, captured_at: datetime) -> tuple[list[Obs], list[dict]]:
    text = path.read_text(errors="ignore")
    m = CAP_RE.search(text)
    t = _tz(m.group(1)) if m else captured_at
    db = sqlite3.connect(":memory:")
    db.executescript(text)
    obs: list[Obs] = []
    coverage: list[dict] = []
    scope = {}
    tables = [r[0] for r in db.execute("select name from sqlite_master where type='table'")]
    if "snapshot_scope" in tables:
        for sysn, cap in db.execute("select origin_system, captured_at from snapshot_scope"):
            scope[sysn] = _tz(cap)
    for tbl in tables:
        cols = [c[1] for c in db.execute(f'pragma table_info("{tbl}")')]
        base = re.sub(r"_(primary|failover_replica|read_replica|reporting_replica|replica_[a-z_]+)$", "", tbl)
        origin = tbl[len(base) + 1:] if tbl != base else "primary"
        if tbl == "snapshot_scope":
            continue
        rows = [dict(zip(cols, r)) for r in db.execute(f'select * from "{tbl}"')]
        sysname = None
        if "record_json" in cols:
            seen_sys = set()
            for r in rows:
                rec = json.loads(r["record_json"])
                rid = rec.get("resource_id") or r["resource_id"]
                ids = _new_ids()
                _collect_ids(rec, ids)
                rec2 = dict(rec)
                st, attrs = _state_for(rid, rec2, ids, collection=r.get("collection_name", ""))
                if prefix_of(rid) == "search":
                    doc = rec.get("document") or {}
                    ids = _new_ids(); _collect_ids(doc, ids)
                rt = None
                for k in ("retained_until", "retention_until"):
                    if rec.get(k):
                        rt = _tz(rec[k])
                tt = _tz(r.get("updated_at")) or t
                o = Obs(fid, t, rid, st, origin=f"{base}.{origin}", ids=ids, attrs=attrs, retained_until=rt,
                        src=r.get("collection_name", ""))
                o.attrs["row_updated_at"] = r.get("updated_at")
                obs.append(o)
                seen_sys.add(r.get("origin_system"))
            for s in seen_sys:
                coverage.append({"file_id": fid, "system": s, "t": t, "origin": f"{base}.{origin}"})
        else:
            tp = None
            for k, v in TYPED_TABLES.items():
                if base == k or base.startswith(k):
                    tp = v
            if tp is None:
                continue
            sysname = {"account": "account_service", "search": "search_service", "analytics": "analytics_platform",
                       "support": "support_platform", "export": "export_service", "data_flow": "integration_service",
                       "backup": "backup_service", "audit_record": "security_service"}[tp]
            for r in rows:
                rid = r["resource_id"]
                ids = _new_ids()
                for k, v in r.items():
                    if k in IDENT_FIELDS_IGNORED or k in ("updated_at", "indexed_at"):
                        continue
                    if k in ("customer_reference", "subject_reference", "source_resource_id"):
                        if isinstance(v, str) and USER_RE.fullmatch(v):
                            ids["user"].add(v)
                        continue
                    _add(ids, v)
                rec = dict(r)
                if tp == "data_flow":
                    rec["enabled"] = bool(r.get("enabled"))
                if tp == "backup":
                    rec["restore_allowed"] = bool(int(r.get("restore_allowed") or 0))
                if tp == "support":
                    rec["message_text"] = r.get("message_excerpt")
                st, attrs = _state_for(rid, rec, ids)
                rt = _tz(r.get("retention_until"))
                o = Obs(fid, t, rid, st, origin=f"{base}.{origin}", ids=ids, attrs=attrs, retained_until=rt, src=base)
                o.attrs["row_updated_at"] = r.get("updated_at")
                if tp == "search" and r.get("source_resource_id"):
                    o.attrs["source_resource_id"] = r["source_resource_id"]
                obs.append(o)
            ts_cov = scope.get(sysname) or t
            coverage.append({"file_id": fid, "system": sysname, "t": ts_cov, "origin": f"{base}.{origin}"})
    for sysn, cap in scope.items():
        if not any(c["system"] == sysn and c["file_id"] == fid for c in coverage):
            coverage.append({"file_id": fid, "system": sysn, "t": cap, "origin": "scope"})
    return obs, coverage


# ----------------------------------------------------------------- JSON
LOOKUP_STATE = {
    "NOT_FOUND": "ABSENT", "FOUND": "PRESENT", "IDENTIFIABLE": "IDENTIFIABLE", "ANONYMIZED": "ANONYMIZED",
    "REDACTED": "REDACTED", "ACTIVE": "ACTIVE", "DISABLED": "DISABLED", "BLOCKED": "BLOCKED",
    "RESTORABLE": "RESTORABLE",
}


def _lookup_obs(fid, t, rid, rtype, result, subj, origin="lookup") -> Obs:
    ids = _new_ids()
    _add(ids, subj)
    st = LOOKUP_STATE.get(str(result).upper(), "PRESENT")
    if rtype == "BACKUP_ARCHIVE" and st == "ACTIVE":
        st = "RESTORABLE"
    if rtype == "DATA_FLOW" and st == "BLOCKED":
        st = "DISABLED"
    o = Obs(fid, t, rid, st, origin=origin, ids=ids, attrs={"record_type": rtype, "result": result})
    if rtype == "SUPPORT_ATTACHMENT":
        o.attrs["attachment"] = True
    return o


def parse_json(fid: str, path: Path, captured_at: datetime) -> tuple[list[Obs], list[dict]]:
    data = json.loads(path.read_text(errors="ignore"))
    obs: list[Obs] = []
    coverage: list[dict] = []
    top_t = _tz(data.get("captured_at") or data.get("exported_at")) if isinstance(data, dict) else captured_at

    # (a) lookup-style record lists
    if isinstance(data, dict) and isinstance(data.get("records"), list) and data["records"] \
            and isinstance(data["records"][0], dict) and "record_type" in data["records"][0]:
        for r in data["records"]:
            obs.append(_lookup_obs(fid, _tz(r.get("recorded_at")) or top_t, r["resource_id"], r.get("record_type"),
                                   r.get("result"), r.get("subject_reference"), origin=data.get("origin", "lookup")))
        return obs, coverage

    def emit(rec: dict, ctx: dict):
        rid = rec.get("resource_id")
        if not rid or not RID_RE.fullmatch(rid):
            return
        ids = _new_ids()
        body = {k: v for k, v in rec.items()}
        prof = rec.get("profile", "__none__")
        absent = False
        if "status_code" in rec and int(rec["status_code"]) == 404:
            absent = True
        if prof is None:
            absent = True
        if isinstance(prof, dict):
            body.update(prof)
        _collect_ids(body, ids)
        ref = body.get("customer_reference") or body.get("user_id") or body.get("subject_reference")
        if isinstance(ref, str) and USER_RE.fullmatch(ref):
            ids["user"].add(ref)
        if prefix_of(rid) == "search" and isinstance(rec.get("document"), dict):
            ids = _new_ids(); _collect_ids(rec["document"], ids)
        st, attrs = _state_for(rid, body, ids)
        if absent:
            st = "ABSENT"
        t = _tz(rec.get("observed_at") or rec.get("captured_at") or ctx.get("t")) or ctx.get("t") or captured_at
        o = Obs(fid, t, rid, st, origin=ctx.get("origin", "json"), ids=ids, attrs=attrs, src=ctx.get("coll", ""))
        for k in ("retention_until", "retained_until"):
            if body.get(k):
                o.retained_until = _tz(body[k])
        obs.append(o)

    def walk(x, ctx):
        if isinstance(x, dict):
            c = dict(ctx)
            for k in ("captured_at", "observed_at", "exported_at"):
                if isinstance(x.get(k), str):
                    c["t"] = _tz(x[k]) or c.get("t")
            if isinstance(x.get("collection"), str):
                c["coll"] = x["collection"]
            if isinstance(x.get("data_store"), str):
                c["origin"] = x["data_store"]
            if "resource_id" in x and RID_RE.fullmatch(str(x["resource_id"])):
                emit(x, c)
                return
            for k, v in x.items():
                cc = dict(c)
                if k in ("account_service", "search_service", "analytics_platform", "support_platform", "export_service",
                         "integration_service", "backup_service", "security_service", "legal_service", "identity_service"):
                    cc["origin"] = k
                walk(v, cc)
        elif isinstance(x, list):
            for v in x:
                walk(v, ctx)

    walk(data, {"t": top_t})
    # coverage: snapshot bundle entries / service exports state which systems were enumerated at which time
    if isinstance(data, dict) and isinstance(data.get("snapshots"), list):
        for s in data["snapshots"]:
            if s.get("data_store"):
                coverage.append({"file_id": fid, "system": s["data_store"], "t": _tz(s.get("captured_at")) or top_t,
                                 "origin": "snapshot:" + str(s.get("collection"))})
    if isinstance(data, dict) and isinstance(data.get("service_exports"), dict):
        for sysn in data["service_exports"]:
            coverage.append({"file_id": fid, "system": sysn, "t": top_t, "origin": "service_export"})
    if isinstance(data, dict) and data.get("source") in ("analytics_identity_store", "analytics_platform"):
        coverage.append({"file_id": fid, "system": "analytics_platform", "t": top_t, "origin": "analytics_report"})
    if isinstance(data, dict) and str(data.get("collection")) == "customer_export_jobs":
        coverage.append({"file_id": fid, "system": "export_service", "t": top_t, "origin": "export_jobs"})
    return obs, coverage


# ----------------------------------------------------------------- text / log
TS_RE = re.compile(r"^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z)\s+(.*)$")
KV_RE = re.compile(r'([A-Za-z_][\w]*)=(?:"((?:[^"\\]|\\.)*)"|(\S+))')
PIPE_RE = re.compile(r"^\|\s*")


def _parse_line(line: str):
    m = TS_RE.match(line.strip())
    if not m:
        return None
    t = _tz(m.group(1))
    rest = m.group(2)
    f: dict = {}
    if rest.startswith("{"):
        try:
            f = {k: (str(v) if not isinstance(v, str) else v) for k, v in json.loads(rest).items()}
            f.setdefault("service", f.get("logger", ""))
            return t, f
        except Exception:
            pass
    mm = re.match(r"^\[(\w+)\]\s+\[([\w]+)\]\s+\[([\w\-]+)\]\s+(\w+):\s*(.*)$", rest)
    if mm:
        f = {"level": mm.group(1), "service": mm.group(2), "trace_id": mm.group(3), "event": mm.group(4)}
        rest = mm.group(5)
    elif " | " in rest and rest.count(" | ") >= 4:
        parts = [p.strip() for p in rest.split(" | ", 5)]
        f = {"level": parts[0], "service": parts[1], "trace_id": parts[2], "event": parts[3]}
        rest = parts[4] if len(parts) > 4 else ""
        if len(parts) > 5:
            rest = parts[4] + " " + parts[5]
    elif "message=" in rest:
        mm2 = re.search(r'message="((?:[^"\\]|\\.)*)"', rest)
        pre = rest[:mm2.start()] if mm2 else rest
        for k, a, b in KV_RE.findall(pre):
            f[k] = a or b
        if "function" in f:
            f["service"] = f["function"]
        if "requestId" in f:
            f["trace_id"] = f["requestId"]
        rest = (mm2.group(1).replace('\\"', '"') if mm2 else "")
    elif re.match(r"^<\d+>\d", rest):
        sm = re.match(r"^<\d+>\d\s+(\S+)\s+(\S+)\s+(\S+)\s+-\s+-\s+(.*)$", rest)
        if sm:
            f["service"], f["trace_id"] = sm.group(2), sm.group(3)
            rest = sm.group(4)
    for k, a, b in KV_RE.findall(rest):
        f.setdefault(k, (a or b).replace('\\"', '"'))
    if "service" not in f and "level" in f:
        pass
    mm3 = re.match(r"^(INFO|WARN|ERROR|DEBUG)\s", rest)
    return t, f


START_KEYS = ("lookup_key", "match_key", "subject_key", "scan_key", "owner_key", "subject_ref", "correlation_key")


def parse_log(fid: str, path: Path, captured_at: datetime) -> tuple[list[Obs], list[dict]]:
    obs: list[Obs] = []
    trace_keys: dict[str, set] = {}
    events = []
    for line in path.read_text(errors="ignore").splitlines():
        r = _parse_line(line)
        if r:
            events.append(r)
    # pass 1: remember identity keys per trace
    for t, f in events:
        tid = f.get("trace_id")
        if not tid:
            continue
        for k in START_KEYS:
            v = f.get(k)
            if v:
                trace_keys.setdefault(tid, set()).add(v)
    for t, f in events:
        ev = f.get("event", "")
        rid = f.get("resource_id")
        tid = f.get("trace_id")
        if ev == "record_lookup":
            obs.append(_lookup_obs(fid, t, rid, f.get("record_type"), f.get("result"), f.get("subject_reference"), "log"))
            continue
        if not rid or not RID_RE.fullmatch(rid):
            continue
        ids = _new_ids()
        for k in ("matched_value", "email", "phone", "email_hash", "customer_email", "owner_email", "requester_email",
                  "requester_phone", "requester_name", "correlation_key", "subject_ref", "subject_reference",
                  "customer_reference", "lookup_key", "subject_key"):
            _add(ids, f.get(k))
        for key in trace_keys.get(tid or "", ()):
            _add(ids, key)
        p = prefix_of(rid)
        st = None
        attrs: dict = {"event": ev}
        rows = f.get("rows")
        if ev == "profile_lookup_completed":
            st = "PRESENT" if (rows not in (None, "0") and f.get("http_status", "200") == "200") else "ABSENT"
        elif ev == "index_query_completed":
            st = "PRESENT" if f.get("result") == "hit" else "ABSENT"
        elif ev in ("message_pii_scan",):
            st = "IDENTIFIABLE"
        elif ev == "attachment_pii_scan":
            st = "IDENTIFIABLE"; attrs["attachment"] = True; attrs["file"] = f.get("file")
        elif ev == "message_read":
            st = "REDACTED" if "[REDACTED]" in f.get("message_text", "") else "IDENTIFIABLE"
        elif ev in ("support_ticket_read_completed",):
            st = "IDENTIFIABLE" if _has_direct(ids) else "REDACTED"
        elif ev == "analytics_identity_scan":
            st = "IDENTIFIABLE"
        elif ev == "analytics_profile_read":
            st = "ANONYMIZED" if f.get("direct_identifiers") == "0" else "IDENTIFIABLE"
        elif ev == "analytics_profile_read_completed":
            ident = _has_direct(ids) or (f.get("customer_reference") not in (None, "null"))
            st = "IDENTIFIABLE" if ident else "ANONYMIZED"
        elif ev == "contact_lookup_completed":
            # a contact/opt-in record held by the analytics platform is a marketing profile (rule_10: always delete)
            st = "PRESENT"
            attrs["marketing"] = f.get("email_opt_in") is not None
        elif ev == "audit_record_read_completed":
            st = "PRESENT" if f.get("rows", "1") != "0" else "ABSENT"
            if f.get("retention_until"):
                attrs["retention_until"] = f["retention_until"]
        elif ev in ("connector_config_read_completed", "connector_heartbeat", "connector_config_loaded"):
            en = f.get("enabled", "false").lower() == "true"
            if ev != "connector_config_read_completed" and f.get("scheduler_registered") is not None:
                attrs["scheduler"] = f.get("scheduler_registered")
            st = "ACTIVE" if en else "DISABLED"
        elif ev in ("restore_policy_read_completed",):
            if f.get("archive_present", "true").lower() == "false":
                st = "ABSENT"
            else:
                st = "RESTORABLE" if f.get("restore_allowed", "false").lower() == "true" else "BLOCKED"
        elif ev == "restore_candidate_evaluated":
            st = "RESTORABLE" if (f.get("eligible") == "true" and f.get("deletion_block") != "true") else "BLOCKED"
        elif ev == "restore_authorization_completed":
            st = "BLOCKED" if f.get("decision") == "deny" else "RESTORABLE"
        elif ev in ("export_download_check_completed", "export_metadata_read_completed"):
            st = "PRESENT" if f.get("available", "true") == "true" else "ABSENT"
        elif ev == "contact_query_completed":
            st = "PRESENT" if f.get("rows", "1") != "0" else "ABSENT"
        elif ev == "deletion_marker_check":
            attrs["marker_found"] = f.get("marker_found")
            st = "PRESENT" if f.get("marker_found") == "false" else "ABSENT"
        if st is None:
            continue
        # support attachment vs message is decided by the event type / file attribute
        o = Obs(fid, t, rid, st, origin="log:" + (f.get("service") or ""), ids=ids, attrs=attrs, src=ev)
        obs.append(o)
    return obs, []


HANDOVER_RE = re.compile(
    r"^-?\s*(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z)\s*\|\s*(\w+)\s*\|\s*(\w+)\s*\|\s*(\w+)\s*\|\s*(\w+)\s*\|\s*(.+?)\s*$")


def parse_txt(fid: str, path: Path, captured_at: datetime) -> tuple[list[Obs], list[dict]]:
    obs = []
    for line in path.read_text(errors="ignore").splitlines():
        m = HANDOVER_RE.match(line.strip())
        if m:
            t, sysn, rid, rtype, result, subj = m.groups()
            obs.append(_lookup_obs(fid, _tz(t), rid, rtype, result, subj, "handover"))
    return obs, []


# ----------------------------------------------------------------- PDF / images
def pdf_text(path: Path) -> str:
    try:
        return subprocess.run(["pdftotext", "-layout", str(path), "-"], capture_output=True, text=True, timeout=60).stdout
    except Exception:
        try:
            from pypdf import PdfReader
            return "\n".join(p.extract_text() or "" for p in PdfReader(str(path)).pages)
        except Exception:
            return ""


def ocr_image(path: Path) -> str:
    try:
        from PIL import Image, ImageOps
        import pytesseract
        im = Image.open(path).convert("L")
        w, h = im.size
        im = im.resize((w * 2, h * 2))
        return pytesseract.image_to_string(ImageOps.autocontrast(im), config="--psm 6")
    except Exception:
        return ""


@dataclass
class DocFacts:
    """Facts extracted from a PDF/image: raw text plus linked identifiers/resources."""
    file_id: str
    kind: str
    text: str
    rids: set = field(default_factory=set)
    ids: dict = field(default_factory=_new_ids)
    refs: set = field(default_factory=set)       # statement / order / case references
    t: datetime | None = None


def doc_facts(fid: str, kind: str, text: str, captured_at: datetime) -> DocFacts:
    d = DocFacts(fid, kind, text, t=captured_at)
    for m in RID_RE.finditer(text):
        d.rids.add(m.group(0))
    for e in EMAIL_RE.findall(text):
        d.ids["email"].add(norm_email(e))
    for u in USER_RE.findall(text):
        d.ids["user"].add(u)
    for p in PHONE_RE.findall(text):
        d.ids["phone"].add(norm_phone(p))
    for h in HASH_RE.findall(text):
        d.ids["hash"].add(h.lower())
    for r in re.findall(r"\b(?:NST-\d{4}-\d{4}-\d{3,4}|ORD-\d+|CS-\d+|FIN-\d+)\b", text):
        d.refs.add(r)
    return d


def _doc_obs(d: DocFacts) -> list:
    """A document that names a resource id together with identifiers shows that resource present."""
    out = []
    pairs = {}
    if d.kind == "image":      # screenshots list many rows: only identifiers on the same OCR line belong to a row
        for line in d.text.splitlines():
            rs = [m.group(0) for m in RID_RE.finditer(line)]
            if not rs:
                continue
            ids = _new_ids()
            for e in EMAIL_RE.findall(line):
                ids["email"].add(norm_email(e))
            for u in USER_RE.findall(line):
                ids["user"].add(u)
            for r in rs:
                if any(ids.values()):
                    for k, v in ids.items():
                        pairs.setdefault(r, _new_ids())[k].update(v)
    for rid in sorted(d.rids):
        ids = {k: set(v) for k, v in d.ids.items()}
        if d.kind == "image":
            if rid not in pairs:
                continue
            ids = pairs[rid]
        p = prefix_of(rid)
        if p == "support":
            o = Obs(d.file_id, d.t, rid, "PRESENT", origin=d.kind, ids=ids, attrs={"attachment": True}, src="document")
        elif p in ("account", "search", "export"):
            o = Obs(d.file_id, d.t, rid, "PRESENT", origin=d.kind, ids=ids, src="document")
        else:
            continue
        out.append(o)
    return out


# ----------------------------------------------------------------- loader
@dataclass
class EvidenceStore:
    manifest: dict                       # file_id -> {path, origin, captured_at}
    obs: list                            # list[Obs]
    coverage: list                       # list[dict]
    docs: dict                           # file_id -> DocFacts
    by_rid: dict = field(default_factory=dict)

    def index(self):
        self.by_rid = {}
        for o in self.obs:
            self.by_rid.setdefault(o.rid, []).append(o)


def load_evidence(dataset_dir: Path, use_ocr: bool = True, ocr_cache: dict | None = None) -> EvidenceStore:
    dataset_dir = Path(dataset_dir)
    manifest = {}
    with open(dataset_dir / "evidence_files.csv", newline="", encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            manifest[r["file_id"]] = {"path": r["file_path"], "origin": r["origin"],
                                      "captured_at": parse_ts(r["captured_at"])}
    obs: list[Obs] = []
    cov: list[dict] = []
    docs: dict = {}
    for fid, m in manifest.items():
        p = dataset_dir / m["path"]
        if not p.exists():
            continue
        ext = p.suffix.lower()
        try:
            if ext == ".sql":
                o, c = parse_sql(fid, p, m["captured_at"])
            elif ext == ".json":
                o, c = parse_json(fid, p, m["captured_at"])
            elif ext == ".log":
                o, c = parse_log(fid, p, m["captured_at"])
            elif ext == ".txt":
                o, c = parse_txt(fid, p, m["captured_at"])
            elif ext == ".pdf":
                d = doc_facts(fid, "pdf", pdf_text(p), m["captured_at"])
                docs[fid] = d
                o, c = _doc_obs(d), []
            elif ext in (".png", ".jpg", ".jpeg"):
                key = m["path"]
                if ocr_cache is not None and key in ocr_cache:
                    txt = ocr_cache[key]
                else:
                    txt = ocr_image(p) if use_ocr else ""
                    if ocr_cache is not None:
                        ocr_cache[key] = txt
                d = doc_facts(fid, "image", txt, m["captured_at"])
                docs[fid] = d
                o, c = _doc_obs(d), []
            else:
                o, c = [], []
        except Exception as exc:  # keep going: one bad file must not stop the audit
            o, c = [], []
            docs[fid] = DocFacts(fid, "error", f"parse error: {exc}")
        obs.extend(o)
        cov.extend(c)
    store = EvidenceStore(manifest, obs, cov, docs)
    store.index()
    return store
