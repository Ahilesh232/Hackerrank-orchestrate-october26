# Interview preparation notes

**One-line pitch.** A deterministic audit engine that reconstructs what personal data still exists at a point in time,
across backups, replicas, search indexes, logs and attachments, and produces an ordered, evidence-cited plan.

**Why deterministic, not an LLM agent.** Compliance outputs must be reproducible and checkable. Each status comes from
explicit policy rules. The LLM would only handle text that the rules cannot classify, and it cannot override an explicit
negative cue. This is also cheaper: the full 518-row run made zero model calls.

**Design decisions you can defend.**
* Observation time decides *state*; capture time decides *citation*. A bundle exported after `audit_at` can still
  describe the state at `audit_at`, but it cannot be cited as evidence available at `audit_at`.
* Reported statuses and workflow activity are claims. Only observed records count.
* Ambiguous links are dropped, not guessed. The trade-off is recall for precision.
* Steps are generated from policy tables and sorted by a fixed phase/operation order, so the output is reproducible.

**Likely questions and honest answers.**
* *How accurate is it?* Status 100% and exact rows 56% on the 32 public samples, tuned on those same samples. Expect lower
  on hidden data. I can name the known misses.
* *What is the weakest part?* Linking accounts that carry no identifier. It relies on timing, and it is the first thing
  I would replace with a stronger join key if the data allowed one.
* *What would you do with more time?* Run the Groq intent and vision layer end to end, add a timing model that is fitted
  on held-out cases, and add property tests for the output schema.
* *Cost?* Deterministic path: no model calls. The optional LLM path is metered per token in `usage_report.md`.
* *Security?* Keys come from environment variables and `.env` is git-ignored. Logs redact secrets and personal data.

**Demo script (3 minutes).** Run `streamlit run code/app.py`, load the sample file, open a COMPLETE case that was reported
INCOMPLETE, show the linked resources and their evidence, then show the evaluation tab and the download buttons.
