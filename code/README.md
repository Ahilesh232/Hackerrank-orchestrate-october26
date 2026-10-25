# Ghost Data Still Exists - deletion audit agent

A deterministic audit engine that checks, for every deletion request, whether personal data is really gone at the case's
`audit_at` time, and writes `output.csv` (one row per case: `case_id`, `deletion_status`, `steps_json`).
A Streamlit UI sits on top of the same engine. An optional Groq layer interprets ambiguous request text only.

## Layout
```
code/
  main.py            CLI: builds output.csv for all requests and validates it
  app.py             Streamlit UI (dataset selection, audit, inspection, metrics, downloads)
  ghostaudit/
    common.py        resource-prefix -> system/category maps, identifier regexes
    evidence.py      parsers: SQLite snapshots, JSON, 6 log styles, handover text, PDF text, screenshot OCR
    identity.py      email -> user_id, using current and historical identities (user_audit_log.csv)
    intent.py        deletion vs non-deletion request rules (English + several other languages)
    timeline.py      which observations may be used for a case
    policy.py        deletion_rules.csv -> finding / remediation / verification per resource
    engine.py        linking, state at audit_at, plan building, phase/op/system/resource ordering
    validate.py      strict schema, ordering and evidence-date checks
    evaluate.py      scoring against sample_output.csv (status, step, field, sequence metrics)
    llm.py           Groq client: retries, disk cache, token and cost tracking
    report.py        usage report writer
  tests/             unit tests, synthetic custom cases, dataset integration tests
  evaluation/        evaluation_report.md, usage_report.md, evaluation/main.py (regenerates both)
  docs/              development transcript and interview preparation notes
```

## Setup
```bash
pip install -r requirements.txt
# system tools used by the evidence readers
sudo apt-get install -y poppler-utils tesseract-ocr     # pdftotext and OCR; optional: --no-ocr skips OCR
cp .env.example .env                                     # only needed for the Groq option
```
Python 3.10+ is required. The dataset is not part of this archive. Pass its path with `--dataset`.

## Run
```bash
# full submission: 518 rows, validated against schema, ordering and evidence-date rules
python3 code/main.py --dataset /path/to/dataset --output output.csv

# score the engine against the 32 sample ground-truth cases
python3 code/main.py --dataset /path/to/dataset --requests sample_deletion_requests.csv --output /tmp/sample.csv
python3 code/evaluation/main.py /path/to/dataset     # rewrites evaluation/evaluation_report.md and usage_report.md

# optional LLM layer (ambiguous request text only)
python3 code/main.py --dataset /path/to/dataset --use-llm

# UI
streamlit run code/app.py
pytest code/tests -q
```

## Method
1. **Intent.** Explicit non-deletion cues (access, export, correction, marketing opt-out, keep-the-account) return
   `NOT_APPLICABLE` with `[]`. Otherwise deletion is assumed for the workflow (see the limitation below).
2. **Identity.** The request email resolves to exactly one `user_id`, via current or historical emails, phones and hashes.
3. **Linking.** A resource is linked to the subject when evidence carries one of the subject's identifiers inside
   `[requested_at, audit_at]`. Accounts and anonymised analytics profiles without any identifier are linked only through
   the activity stream, and only when the link is unambiguous.
4. **State at audit_at.** The latest observation inside the window is used. Reported statuses and activity results are
   never taken as proof. Citations only point to files captured at or before `audit_at`.
5. **Policy.** `deletion_rules.csv` decides the treatment per category (DELETE, REDACT, ANONYMIZE, RETAIN, flow disable,
   restore block). A violation yields a finding, a remediation step and a verification step. Compliant resources get a
   verification step only.
6. **Status.** `INCOMPLETE` if any violation remains, otherwise `COMPLETE`. `NOT_APPLICABLE` for non-deletion requests.
7. **Output.** Steps are sorted by phase, then operation order, system and resource, and numbered from 1.

## Results and limitations (see evaluation/ for the measured numbers)
* On the 32 sample cases, status accuracy is 100%, and exact row match is 56%. Those cases were used during development,
  and the timing windows were tuned on them, so the scores are optimistic.
* Three ground-truth resources (`account_004`, `audit_record_6645`, `audit_record_5569`) appear in no file. They cannot
  be derived without inventing evidence, so they are not produced.
* Accounts and anonymised analytics profiles with no identifier in any artefact are linked only by the activity-window
  heuristic. Ambiguous groups stay unlinked, which costs recall.
* No Groq call was made in the reported run: the sandbox could not resolve api.groq.com and no key was available.
  The LLM path is unit-tested against a mocked transport only.
* The 518-row file contains no ambiguous request texts, so the rule-based intent decision was used for every row.

## Tests
`pytest code/tests -q` runs unit tests, three synthetic custom cases built in `tests/conftest.py`, and integration tests
against the dataset (skipped when it is absent).
