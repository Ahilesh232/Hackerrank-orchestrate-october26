"""Identity resolution: request email -> stable user_id plus every current/historical identifier."""
from __future__ import annotations

import csv
from dataclasses import dataclass, field
from pathlib import Path

from .common import norm_email, norm_phone


@dataclass
class Identity:
    user_id: str
    name: str = ""
    emails: set = field(default_factory=set)       # normalised, current + historical
    phones: set = field(default_factory=set)
    hashes: set = field(default_factory=set)
    current_email: str = ""
    historical_emails: set = field(default_factory=set)
    matched_via: str = ""                          # "current_email" | "historical_email"

    def ids(self):
        return {"email": self.emails, "phone": self.phones, "hash": self.hashes, "user": {self.user_id}}


class IdentityResolver:
    def __init__(self, dataset_dir: Path):
        d = Path(dataset_dir)
        self.users: dict[str, Identity] = {}
        self.email_to_users: dict[str, set] = {}
        self.current_email_to_user: dict[str, str] = {}
        with open(d / "user_identities.csv", newline="", encoding="utf-8") as fh:
            for r in csv.DictReader(fh):
                u = Identity(r["user_id"], r["name"], current_email=norm_email(r["email"]))
                u.emails.add(norm_email(r["email"]))
                if r["phone"]:
                    u.phones.add(norm_phone(r["phone"]))
                if r["email_hash"]:
                    u.hashes.add(r["email_hash"].lower())
                self.users[u.user_id] = u
                self.current_email_to_user[norm_email(r["email"])] = u.user_id
        with open(d / "user_audit_log.csv", newline="", encoding="utf-8") as fh:
            self.audit = list(csv.DictReader(fh))
        for a in self.audit:
            u = self.users.get(a["user_id"])
            if not u:
                continue
            f = a["field_name"]
            for v in (a["old_value"], a["new_value"]):
                if not v:
                    continue
                if f == "email":
                    u.emails.add(norm_email(v))
                    if norm_email(v) != u.current_email:
                        u.historical_emails.add(norm_email(v))
                elif f == "phone":
                    u.phones.add(norm_phone(v))
                elif f == "email_hash":
                    u.hashes.add(v.lower())
        for u in self.users.values():
            for e in u.emails:
                self.email_to_users.setdefault(e, set()).add(u.user_id)

    def resolve(self, email: str) -> Identity | None:
        e = norm_email(email)
        uid = self.current_email_to_user.get(e)
        if uid:
            u = self.users[uid]
            u.matched_via = "current_email"
            return u
        cands = sorted(self.email_to_users.get(e, ()))
        if not cands:
            return None
        u = self.users[cands[0]]
        u.matched_via = "historical_email"
        return u

    def audit_ids(self, user_id: str):
        return [a["audit_id"] for a in self.audit if a["user_id"] == user_id]
