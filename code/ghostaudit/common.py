"""Shared constants and helpers (resource taxonomy, identifier regexes, time helpers)."""
from __future__ import annotations
import re
from datetime import datetime, timezone

EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
HASH_RE = re.compile(r"\b[0-9a-f]{64}\b", re.I)
USER_RE = re.compile(r"\buser_\d+\b")
PHONE_RE = re.compile(r"\+\d[\d ()\-]{6,}\d")
RID_RE = re.compile(
    r"\b(account|search|support|analytics|export|backup|data_flow|audit_record|tombstone|marketing|cache|email_delivery|legal_hold|object)_(\d+)\b")

# resource-id prefix -> (system_id, data category used by deletion_rules.csv)
PREFIX_SYSTEM = {
    "account": "account_service",
    "search": "search_service",
    "support": "support_platform",
    "analytics": "analytics_platform",
    "export": "export_service",
    "data_flow": "integration_service",
    "backup": "backup_service",
    "audit_record": "security_service",
    "tombstone": "identity_service",
    "marketing": "marketing_platform",
    "cache": "cache_service",
    "email_delivery": "email_delivery_service",
    "legal_hold": "legal_service",
    "object": "support_platform",
}
PREFIX_CATEGORY = {
    "account": "USER_PROFILE", "search": "SEARCH_INDEX_ENTRY", "support": "SUPPORT_MESSAGE",
    "analytics": "ANALYTICS_PROFILE", "export": "USER_EXPORT", "data_flow": "DATA_FLOW",
    "backup": "BACKUP_ARCHIVE", "audit_record": "SECURITY_AUDIT_LOG", "tombstone": "ACCOUNT_TOMBSTONE",
    "marketing": "MARKETING_PROFILE", "cache": "CACHE_ENTRY", "email_delivery": "EMAIL_DELIVERY_LOG",
    "legal_hold": "LEGAL_HOLD_RECORD", "object": "SUPPORT_ATTACHMENT",
}


def prefix_of(rid: str) -> str:
    m = RID_RE.fullmatch(rid or "")
    return m.group(1) if m else ""


def system_of(rid: str) -> str:
    return PREFIX_SYSTEM.get(prefix_of(rid), "")


def norm_email(v: str) -> str:
    return (v or "").strip().lower()


def norm_phone(v: str) -> str:
    return re.sub(r"\D", "", v or "")


def parse_ts(s: str) -> datetime:
    s = (s or "").strip().replace("Z", "+00:00")
    d = datetime.fromisoformat(s)
    if d.tzinfo is None:
        d = d.replace(tzinfo=timezone.utc)
    return d


def ts_str(d: datetime) -> str:
    return d.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def classify_identifier(v) -> tuple[str, str] | None:
    """Return (kind, normalised value) for an identifier-looking scalar, else None."""
    if not isinstance(v, str):
        return None
    v = v.strip()
    if not v or v.lower() in ("null", "none", "[redacted]"):
        return None
    if EMAIL_RE.fullmatch(v):
        return "email", norm_email(v)
    if HASH_RE.fullmatch(v):
        return "hash", v.lower()
    if USER_RE.fullmatch(v):
        return "user", v
    if PHONE_RE.fullmatch(v):
        return "phone", norm_phone(v)
    return None
