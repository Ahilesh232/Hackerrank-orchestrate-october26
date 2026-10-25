# Development transcript (condensed)

This is a condensed record of the development session. The full chat transcript is the conversation itself; nothing
here is invented. Numbers are quoted from runs listed in `evaluation/`.

1. **Inspection.** Unzipped the archive. Read the problem statement, README, AGENTS.md (logging and challenge rules) and
   `dataset/` (CSV schemas, `deletion_rules.csv`, `sample_output.csv`). Found 94 evidence files: SQLite snapshots
   (executed in an in-memory SQLite), JSON bundles and exports, six log formats, handover text, 4 PDFs and 11 screenshots.
2. **Ground-truth study.** Mapped sample ground-truth resources to evidence. Found that some resources appear only in
   `system_activity.csv` and that three never appear in any artefact.
3. **Engine.** Built the modules in `code/ghostaudit/`. First measured version: 75% status accuracy on the samples.
4. **Fixes made during development** (each one is in the evaluation report's table):
   * An over-broad negative cue ("copy of") flipped deletion requests to NOT_APPLICABLE.
   * Deletion-verb regex missed "deleting"; the no-cue default was changed to deletion.
   * State was taken from the wrong time window, and bundles exported after `audit_at` were dropped. State now uses
     observation time; only citations are limited to capture time.
   * PDF and screenshot facts were not linked. Screenshot identifiers are now paired per OCR line.
   * The account-number convention and activity-window links produced false positives. Both are now restricted.
   * Pre-request observations and links after `audit_at` were leaking into plans.
5. **Final measured run.** Full 518-request file: 155 COMPLETE, 353 INCOMPLETE, 10 NOT_APPLICABLE. Schema, ordering and
   evidence-date validation passes. 9 tests pass.
6. **Groq.** Attempted; DNS resolution of api.groq.com failed in the sandbox and no key was set. No model calls were made.
7. **Mistakes to own.** The first version of the usage text said "the audit engine is deterministic" without saying
   that the timing windows were tuned on the samples. That was corrected in the evaluation report.
8. **Logging.** AGENTS.md asks for a log entry per turn. Earlier turns of this session were not logged to `log.txt`
   during the session. The log file in this archive starts from the final turn, and says so.
