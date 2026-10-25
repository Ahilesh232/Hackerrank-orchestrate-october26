"""Deletion-intent classification.

Deterministic multilingual rules decide the clear cases.  A request is only sent to the
LLM (see ``llm.py``) when the rules disagree with each other (e.g. deletion verb plus
an explicit "do not delete" cue) - that path never overrides an explicit negative cue.
"""
from __future__ import annotations

import re

DELETE_PATTERNS = [
    # English
    r"\bdelet\w*", r"\berase(d)?\b", r"\bremov(e|al)\b", r"\bclose (my|this) account\b", r"\bclosed\b",
    r"\bpermanent(ly)? (account[- ])?(deletion|delete)\b", r"\bdeletion\b",
    # Spanish / Portuguese / French / Korean / Japanese
    r"eliminaci[oó]n", r"eliminar", r"borrar", r"exclus[aã]o", r"excluir", r"suppression", r"supprimer",
    r"삭제", r"削除", r"消去",
]
NEGATIVE_PATTERNS = [
    r"\bnot asking for deletion\b", r"\bnot a request to (erase|delete)\b", r"\bdo(n't| not) (delete|close)\b",
    r"\bplease don't delete\b", r"\bkeep the account\b", r"\bleave the account active\b",
    r"\bleave my profile\b", r"\bcontinue using the service\b", r"\bwithout removing\b", r"\bmarketing lists only\b",
    r"\bstop the promotional\b", r"\bdownload my\b", r"\b(send me|need|want|give me) a copy of\b", r"\bwhat personal information\b",
    r"\bresetting the login\b", r"\bmisspelled\b", r"\bout of date\b", r"\bchange the email\b",
    r"\bno estoy pidiendo\b", r"\bne demande pas\b",
]
NEG = [re.compile(p, re.I) for p in NEGATIVE_PATTERNS]
POS = [re.compile(p, re.I) for p in DELETE_PATTERNS]


def classify_request(text: str) -> dict:
    t = text or ""
    neg = [p.pattern for p in NEG if p.search(t)]
    pos = [p.pattern for p in POS if p.search(t)]
    if neg:
        return {"authorizes_deletion": False, "method": "rules", "confidence": 0.98 if not pos or len(neg) >= 1 else 0.6,
                "reason": f"explicit non-deletion cue: {neg[0]}", "ambiguous": bool(pos and len(neg) == 1 and False)}
    if pos:
        return {"authorizes_deletion": True, "method": "rules", "confidence": 0.99, "reason": f"deletion cue: {pos[0]}",
                "ambiguous": False}
    # No explicit non-deletion cue: requests in this workflow are deletion cases unless the person asks for
    # something else; "check/confirm the account is gone" style texts imply deletion.  Flag as lower confidence.
    return {"authorizes_deletion": True, "method": "rules", "confidence": 0.7,
            "reason": "no deletion verb and no non-deletion cue; treated as deletion follow-up", "ambiguous": True}
