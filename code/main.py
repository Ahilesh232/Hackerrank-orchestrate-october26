#!/usr/bin/env python3
"""CLI: python3 code/main.py [--dataset dataset] [--output output.csv] [--use-llm] [--limit N]

Reads dataset/, audits every request in deletion_requests.csv and writes output.csv (default: repo root)."""
from __future__ import annotations


import argparse
import csv
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent

try:  # optional: read GROQ_API_KEY etc. from code/.env
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).resolve().parent / ".env")
except ImportError:
    pass

sys.path.insert(0, str(HERE))

from ghostaudit.engine import AuditEngine, load_cases, steps_to_json  # noqa: E402
from ghostaudit.validate import validate_output  # noqa: E402


def run(dataset: Path, output: Path, requests_file: str = "deletion_requests.csv", use_llm: bool = False,
        limit: int | None = None, progress=None):
    cases = load_cases(dataset / requests_file)
    eng = AuditEngine(dataset)
    llm = None
    if use_llm:
        from ghostaudit.llm import GroqClient
        llm = GroqClient.from_env()
    pool = cases + ([c for c in load_cases(dataset / "sample_deletion_requests.csv")]
                    if requests_file == "deletion_requests.csv" and (dataset / "sample_deletion_requests.csv").exists() else [])
    eng.prepare(pool)
    results = []
    for i, c in enumerate(cases[:limit] if limit else cases):
        intent = None
        if llm is not None:
            from ghostaudit.intent import classify_request
            intent = classify_request(c.request_text)
            if intent["ambiguous"]:
                intent = llm.classify_intent(c.request_text, fallback=intent)
        results.append(eng.audit(c, intent))
        if progress:
            progress(i + 1, len(cases))
    results.sort(key=lambda r: r.case.case_id)
    output.parent.mkdir(parents=True, exist_ok=True)
    with open(output, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh, lineterminator="\n")
        w.writerow(["case_id", "deletion_status", "steps_json"])
        for r in results:
            w.writerow([r.case.case_id, r.status, steps_to_json(r.steps)])
    return eng, results, llm


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dataset", default=str(HERE.parent / "dataset"))
    ap.add_argument("--output", default=str(HERE.parent / "output.csv"))
    ap.add_argument("--requests", default="deletion_requests.csv")
    ap.add_argument("--use-llm", action="store_true", help="use Groq for ambiguous request text (needs GROQ_API_KEY)")
    ap.add_argument("--limit", type=int)
    a = ap.parse_args()
    eng, results, llm = run(Path(a.dataset), Path(a.output), a.requests, a.use_llm, a.limit)
    from collections import Counter
    print("rows:", len(results), dict(Counter(r.status for r in results)))
    if a.requests == "deletion_requests.csv" and not a.limit:
        errs = validate_output(Path(a.output), Path(a.dataset))
        print("validation:", "OK" if not errs else f"{len(errs)} problems")
        for e in errs[:20]:
            print("  ", e)
        if errs:
            sys.exit(1)
    usage = llm.usage_summary() if llm else None
    if usage:
        print("llm usage:", usage)
    if a.requests == "deletion_requests.csv" and not a.limit:
        from ghostaudit.report import usage_report_md
        from ghostaudit.llm import DEFAULT_TEXT_MODEL, DEFAULT_VISION_MODEL
        rep = HERE / "evaluation" / "usage_report.md"
        rep.parent.mkdir(exist_ok=True)
        rep.write_text(usage_report_md(len(results), usage, getattr(llm, "text_model", DEFAULT_TEXT_MODEL),
                                       getattr(llm, "vision_model", DEFAULT_VISION_MODEL), True))
        print("wrote", rep)


if __name__ == "__main__":
    main()
