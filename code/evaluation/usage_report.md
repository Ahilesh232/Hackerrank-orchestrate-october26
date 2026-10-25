# Usage report - final full-dataset run (518 requests)

Generated 2026-10-25 01:46 UTC by `python3 code/main.py`. All figures below are measured from the run that produced `output.csv`.

## Result: no model calls were made in this run

| Provider | Model | Model calls | Input tokens | Output tokens | Total tokens | Est. cost (USD) |
| --- | --- | ---: | ---: | ---: | ---: | ---: |
| Groq | llama-3.3-70b-versatile (configured, not invoked) | 0 | 0 | 0 | 0 | 0.00 |
| Groq | meta-llama/llama-4-scout-17b-16e-instruct (configured, not invoked) | 0 | 0 | 0 | 0 | 0.00 |
| **Overall** | | **0** | **0** | **0** | **0** | **0.00** |

* Average tokens per request: 0. Estimated cost per request: $0.00.
* Why: the audit engine is deterministic. Request intent is decided by multilingual rules (no ambiguous request texts required the model in the 518-row file), evidence is parsed with SQLite / JSON / log parsers / `pdftotext`, and screenshots are read with Tesseract OCR (enabled) instead of a vision model.
* The Groq path (`--use-llm`) exists and is unit-tested with a mocked transport, but it was **not exercised against the live API** because no `GROQ_API_KEY` was available and the sandbox could not reach api.groq.com. No token or cost number has been estimated or invented for it. Re-run with `--use-llm` and a key to populate real figures.
