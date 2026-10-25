"""Temporal selection: which observations may be used for a case and what is the state at audit_at."""
from __future__ import annotations

from .common import prefix_of


def eligible(o, store, requested_at, audit_at, for_state=True):
    cap = store.manifest[o.file_id]["captured_at"]
    if o.t is not None and (o.t > audit_at):
        return False
    if o.t is not None and requested_at is not None and o.t < requested_at:
        return False                        # pre-request observations describe a different lifecycle
    return True


def latest(obs_list):
    """Latest observation by (observation time, row update time, file capture)."""
    def key(o):
        return (o.t.timestamp() if o.t else 0, o.attrs.get("row_updated_at") or "", o.file_id)
    return sorted(obs_list, key=key)[-1] if obs_list else None
