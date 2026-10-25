"""Scoring against ground-truth outputs (sample_output.csv or user-created cases)."""
from __future__ import annotations

import csv
import json
from collections import Counter

FIELDS = ["phase", "system_id", "resource_id", "operation", "state", "evidence_id"]


def read_output(path):
    with open(path, newline="", encoding="utf-8") as fh:
        return {r["case_id"]: (r["deletion_status"], json.loads(r["steps_json"])) for r in csv.DictReader(fh)}


def _tuple(s, with_ev=True):
    t = (s["phase"], s["system_id"], s["resource_id"], s["operation"], s["state"])
    return t + ((s["evidence_id"],) if with_ev else ())


def _lcs(a, b):
    if not a or not b:
        return 0
    prev = [0] * (len(b) + 1)
    for x in a:
        cur = [0]
        for j, y in enumerate(b, 1):
            cur.append(prev[j - 1] + 1 if x == y else max(prev[j], cur[-1]))
        prev = cur
    return prev[-1]


def score(pred: dict, gold: dict) -> dict:
    ids = sorted(gold)
    status_ok = sum(pred.get(c, ("", []))[0] == gold[c][0] for c in ids)
    tp = fp = fn = 0
    tp_ne = fp_ne = fn_ne = 0
    field_hit = Counter(); field_tot = Counter()
    exact_rows = 0; seq_ok = 0; lcs_sum = 0.0
    per_case = {}
    for c in ids:
        gs, ps = gold[c][1], pred.get(c, ("", []))[1]
        g, p = Counter(_tuple(s) for s in gs), Counter(_tuple(s) for s in ps)
        inter = sum((g & p).values()); tp += inter; fp += sum(p.values()) - inter; fn += sum(g.values()) - inter
        g2, p2 = Counter(_tuple(s, False) for s in gs), Counter(_tuple(s, False) for s in ps)
        i2 = sum((g2 & p2).values()); tp_ne += i2; fp_ne += sum(p2.values()) - i2; fn_ne += sum(g2.values()) - i2
        pk = {(s["phase"], s["system_id"], s["resource_id"], s["operation"]): s for s in ps}
        for s in gs:
            k = (s["phase"], s["system_id"], s["resource_id"], s["operation"])
            for f in FIELDS:
                field_tot[f] += 1
                if k in pk and pk[k][f] == s[f]:
                    field_hit[f] += 1
            field_tot["step_no"] += 1
            if k in pk and pk[k]["step_no"] == s["step_no"]:
                field_hit["step_no"] += 1
        gl = [_tuple(s, False) for s in gs]; pl = [_tuple(s, False) for s in ps]
        l = _lcs(gl, pl); denom = max(len(gl), len(pl)) or 1
        lcs_sum += l / denom
        row_exact = (gold[c][0] == pred.get(c, ("", []))[0] and [_tuple(s) for s in gs] == [_tuple(s) for s in ps])
        exact_rows += row_exact
        seq_ok += (gl == pl)
        per_case[c] = {"status_ok": pred.get(c, ("", []))[0] == gold[c][0], "exact": row_exact,
                       "missing": sorted(set(g - p)), "extra": sorted(set(p - g))}
    prf = lambda tp, fp, fn: (tp / (tp + fp) if tp + fp else 1.0, tp / (tp + fn) if tp + fn else 1.0)
    pr, rc = prf(tp, fp, fn); pr2, rc2 = prf(tp_ne, fp_ne, fn_ne)
    f1 = lambda p, r: 2 * p * r / (p + r) if p + r else 0.0
    n = len(ids)
    return {
        "cases": n, "status_accuracy": status_ok / n,
        "row_exact_match": exact_rows / n,
        "step_precision": pr, "step_recall": rc, "step_f1": f1(pr, rc),
        "step_f1_ignoring_evidence": f1(pr2, rc2),
        "field_accuracy": {f: field_hit[f] / field_tot[f] for f in field_tot},
        "sequence_exact_order": seq_ok / n, "sequence_lcs_ratio": lcs_sum / n,
        "counts": {"tp": tp, "fp": fp, "fn": fn}, "per_case": per_case,
    }
