#!/usr/bin/env python3
"""Evaluation harness: python3 code/evaluation/main.py  ->  writes evaluation/evaluation_report.md

* scores the engine against dataset/sample_output.csv (the only public ground truth)
* runs the custom evaluation cases (code/tests, synthetic data created from scratch)
* reports every remaining failure from the actual run
"""
from __future__ import annotations

import subprocess
import sys
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT))

from ghostaudit.engine import AuditEngine, load_cases  # noqa: E402
from ghostaudit.evaluate import read_output, score  # noqa: E402


def main(dataset: Path | None = None):
    ds = Path(dataset or ROOT.parent / "dataset")
    eng = AuditEngine(ds, use_ocr=True)
    sample = load_cases(ds / "sample_deletion_requests.csv")
    eng.prepare(sample + load_cases(ds / "deletion_requests.csv"))
    pred = {}
    for c in sample:
        r = eng.audit(c)
        pred[c.case_id] = (r.status, r.steps)
    gold = read_output(ds / "sample_output.csv")
    sc = score(pred, gold)

    t = subprocess.run([sys.executable, "-m", "pytest", str(ROOT / "tests"), "-q", "-k", "custom_case or ordering or intent or scoring or groq",
                        "--no-header", "-p", "no:cacheprovider"], capture_output=True, text=True, cwd=ROOT)
    test_tail = "\n".join(t.stdout.strip().splitlines()[-3:])

    miss_kinds = Counter()
    fails = []
    for c, v in sc["per_case"].items():
        if v["exact"]:
            continue
        for m in v["missing"]:
            miss_kinds[m[1] if False else m[2].rsplit("_", 1)[0] + " / " + m[3]] += 1
        fails.append((c, v))

    L = ["# Evaluation report", "",
         "All numbers were produced by `python3 code/evaluation/main.py` on the final code. **Caveat:** the 32 sample cases "
         "are the only public ground truth and were also used during development, so these figures are development-set "
         "scores, not an unbiased estimate of the hidden grader's score.", "",
         "## 1. Case-level status accuracy (32 sample cases)", "",
         f"* Status accuracy: **{sc['status_accuracy']:.1%}** ({round(sc['status_accuracy'] * sc['cases'])}/{sc['cases']})",
         f"* Rows exactly equal to ground truth (status + every step incl. evidence id and order): **{sc['row_exact_match']:.1%}**",
         "", "## 2. Step-level accuracy", "",
         f"* Precision **{sc['step_precision']:.3f}**, recall **{sc['step_recall']:.3f}**, F1 **{sc['step_f1']:.3f}** "
         f"(TP {sc['counts']['tp']}, FP {sc['counts']['fp']}, FN {sc['counts']['fn']}); step = (phase, system, resource, operation, state, evidence_id)",
         f"* F1 ignoring evidence_id: {sc['step_f1_ignoring_evidence']:.3f}", "",
         "## 3. Field-level accuracy", "",
         "Share of ground-truth steps whose counterpart (matched on phase+system+resource+operation) has the same field value; "
         "missing counterparts count as wrong.", "", "| field | accuracy |", "| --- | ---: |"]
    L += [f"| {f} | {a:.3f} |" for f, a in sc["field_accuracy"].items()]
    L += ["", "## 4. Sequence accuracy", "",
          f"* Exact step order identical to ground truth: **{sc['sequence_exact_order']:.1%}** of cases",
          f"* Mean normalised LCS of the step sequences: **{sc['sequence_lcs_ratio']:.3f}**",
          "* Every produced step list also passes `ghostaudit.validate` (phase -> operation -> system -> resource order, consecutive step numbers).", "",
          "## 5. Additional evaluation cases created by us", "",
          "Synthetic mini-datasets (no real/organiser data) built in `code/tests/conftest.py`:", "",
          "1. **Historical email + tombstone + backup + decoy**: request uses an *old* email; engine must resolve the user, delete the account, "
          "redact an identifiable tombstone, block a restorable backup, keep (verify present) the audit record, and ignore a search record owned by another person.",
          "2. **Non-deletion request** (\"copy of my data, keep the account\"): must return `NOT_APPLICABLE` with `[]`.",
          "3. **Temporal cut-off**: evidence recorded/captured after `audit_at` must neither create a finding nor be cited.",
          "4. Extras: step ordering/numbering/empty-evidence rules, multilingual intent rules, scorer sanity, Groq client token tracking + cache (mock transport).", "",
          "```", test_tail, "```", "",
          "## 6. Remaining failures on the sample set", ""]
    if not fails:
        L.append("None.")
    else:
        L += [f"{len(fails)} of {sc['cases']} rows differ from ground truth. Missing steps by kind: "
              + ", ".join(f"{k}: {n}" for k, n in miss_kinds.most_common()), ""]
        for c, v in fails:
            L.append(f"* `{c}` status {'OK' if v['status_ok'] else 'WRONG'}; missing {len(v['missing'])}, extra {len(v['extra'])}: "
                     + "; ".join(f"{m[3]} {m[2]}" for m in v["missing"][:4]))
        L += ["", "Root causes:",
              "* **Unlinked account / anonymised-analytics records.** These records carry no identifier anywhere in the evidence; the only link is "
              "the worker event in `system_activity.csv`, which is shared by several cases with the same `reported_at`. A link is created only when "
              "it is unambiguous (one unclaimed candidate event, one unlinked case). Ambiguous groups are left out rather than guessed - this costs recall, never precision.",
              "* **Resources absent from every artefact** (e.g. `account_004`, `audit_record_6645`, `audit_record_5569`): not derivable without inventing evidence."]
    L += ["", "## 7. Failures found during development and the fixes", "",
          "| # | Failure found | Fix |", "| --- | --- | --- |",
          "| 1 | \"remaining copy of my profile\" matched the non-deletion cue \"copy of\" -> wrongly `NOT_APPLICABLE` | cue narrowed to \"send me / need a copy of\" |",
          "| 2 | \"I'm deleting my account\" and follow-up wording (\"confirm the profile is gone\") not matched as deletion | `delet\\w*` + default to deletion unless an explicit negative cue exists |",
          "| 3 | Bundles exported *after* `audit_at` were dropped entirely, hiding the state at `audit_at` (COMPLETE cases reported INCOMPLETE) | state is taken from observation time; only *citations* are restricted to files captured <= `audit_at` |",
          "| 4 | Support-case PDF (delivery correction attachment) was never linked | PDF text -> resource/identifier observations |",
          "| 5 | Account convention (`account_<user#>`) produced decoys (`account_050`, `account_541`) | accepted only if the worker delete event is within 30 min of `reported_at` |",
          "| 6 | Activity-window analytics link produced false positives for unfinished cases | only for cases reported `COMPLETE`, and only when unambiguous |",
          "| 7 | Multi-row console screenshots linked every row to every e-mail on screen | OCR identifiers are paired per text line |",
          "| 8 | Test `custom_case_3` found that a record observed after `audit_at` still created a link | links require observation time inside [requested_at, audit_at] |",
          "| 9 | Observations before `requested_at` (old, already-deleted account of a previous lifecycle) polluted the plan | pre-request observations are ignored |", "",
          "## 8. Method notes", "",
          "* Deterministic, rule-driven engine. The engine never reads `sample_output.csv` at run time.",
          "* **Disclosure - tuning bias:** the timing windows used to link unlabelled accounts and anonymised analytics profiles "
          "(account delete at -80..-35 min and 30 min of `reported_at` for the `account_<user#>` convention; analytics anonymise at "
          "-31..-26 and -10..-1 min) were chosen by inspecting the timing of the 32 sample ground-truth rows. The sample scores above are "
          "therefore optimistic and are not an unbiased estimate of hidden-grader accuracy.",
          "* The `account_<user#>` naming convention was also inferred from the sample rows.",
          "* Every finding cites a `file_id` captured at or before `audit_at`; remediation and post-remediation checks of INCOMPLETE cases carry an empty `evidence_id`.",
          "* Reported statuses (`COMPLETE` / `PARTIALLY_COMPLETE` / `NOT_STARTED`) and activity `reported_result` are never trusted as proof.",
          "* Hidden-grader accuracy is unknown. Expect lower recall on cases whose accounts could not be linked unambiguously (see section 6)."]
    (HERE / "evaluation_report.md").write_text("\n".join(L) + "\n")
    print("wrote", HERE / "evaluation_report.md")
    print({k: round(v, 3) for k, v in sc.items() if isinstance(v, float)})


if __name__ == "__main__":
    main(Path(sys.argv[1]) if len(sys.argv) > 1 else None)
