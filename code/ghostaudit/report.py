"""Markdown report writers (numbers always come from an actual run)."""
from __future__ import annotations

from datetime import datetime, timezone


def usage_report_md(n_cases: int, usage: dict | None, text_model: str, vision_model: str, ocr: bool) -> str:
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    out = [f"# Usage report - final full-dataset run ({n_cases} requests)", "",
           f"Generated {ts} by `python3 code/main.py`. All figures below are measured from the run that produced `output.csv`.", ""]
    if not usage or usage["calls"] == 0:
        out += ["## Result: no model calls were made in this run", "",
                "| Provider | Model | Model calls | Input tokens | Output tokens | Total tokens | Est. cost (USD) |",
                "| --- | --- | ---: | ---: | ---: | ---: | ---: |",
                f"| Groq | {text_model} (configured, not invoked) | 0 | 0 | 0 | 0 | 0.00 |",
                f"| Groq | {vision_model} (configured, not invoked) | 0 | 0 | 0 | 0 | 0.00 |",
                "| **Overall** | | **0** | **0** | **0** | **0** | **0.00** |", "",
                f"* Average tokens per request: 0. Estimated cost per request: $0.00.",
                f"* Why: the audit engine is deterministic. Request intent is decided by multilingual rules (no ambiguous request texts "
                f"required the model in the 518-row file), evidence is parsed with SQLite / JSON / log parsers / `pdftotext`, and screenshots "
                f"are read with Tesseract OCR ({'enabled' if ocr else 'disabled'}) instead of a vision model.",
                "* The Groq path (`--use-llm`) exists and is unit-tested with a mocked transport, but it was **not exercised against the "
                "live API** because no `GROQ_API_KEY` was available and the sandbox could not reach api.groq.com. No token or cost number "
                "has been estimated or invented for it. Re-run with `--use-llm` and a key to populate real figures."]
        return "\n".join(out) + "\n"
    out += ["| Provider | Model | Calls | Cache hits | Input tokens | Output tokens | Total tokens | Est. cost (USD) |",
            "| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |"]
    for m, r in usage["models"].items():
        out.append(f"| Groq | {m} | {r['calls']} | {r['cache_hits']} | {r['input_tokens']} | {r['output_tokens']} | "
                   f"{r['total_tokens']} | {r['est_cost_usd']:.6f} |")
    out.append(f"| **Overall** | | **{usage['calls']}** | | **{usage['input_tokens']}** | **{usage['output_tokens']}** | "
               f"**{usage['total_tokens']}** | **{usage['est_cost_usd']:.6f}** |")
    per = max(n_cases, 1)
    out += ["", f"* Average tokens per request: {usage['total_tokens'] / per:.1f}",
            f"* Estimated cost per request: ${usage['est_cost_usd'] / per:.6f}",
            "* Prices are configurable defaults (USD per 1M tokens) - verify against Groq's pricing page."]
    return "\n".join(out) + "\n"
