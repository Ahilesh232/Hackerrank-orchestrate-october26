# Evaluation report

All numbers were produced by `python3 code/evaluation/main.py` on the final code. **Caveat:** the 32 sample cases are the only public ground truth and were also used during development, so these figures are development-set scores, not an unbiased estimate of the hidden grader's score.

## 1. Case-level status accuracy (32 sample cases)

* Status accuracy: **100.0%** (32/32)
* Rows exactly equal to ground truth (status + every step incl. evidence id and order): **56.2%**

## 2. Step-level accuracy

* Precision **1.000**, recall **0.902**, F1 **0.948** (TP 174, FP 0, FN 19); step = (phase, system, resource, operation, state, evidence_id)
* F1 ignoring evidence_id: 0.948

## 3. Field-level accuracy

Share of ground-truth steps whose counterpart (matched on phase+system+resource+operation) has the same field value; missing counterparts count as wrong.

| field | accuracy |
| --- | ---: |
| phase | 0.902 |
| system_id | 0.902 |
| resource_id | 0.902 |
| operation | 0.902 |
| state | 0.902 |
| evidence_id | 0.902 |
| step_no | 0.720 |

## 4. Sequence accuracy

* Exact step order identical to ground truth: **56.2%** of cases
* Mean normalised LCS of the step sequences: **0.878**
* Every produced step list also passes `ghostaudit.validate` (phase -> operation -> system -> resource order, consecutive step numbers).

## 5. Additional evaluation cases created by us

Synthetic mini-datasets (no real/organiser data) built in `code/tests/conftest.py`:

1. **Historical email + tombstone + backup + decoy**: request uses an *old* email; engine must resolve the user, delete the account, redact an identifiable tombstone, block a restorable backup, keep (verify present) the audit record, and ignore a search record owned by another person.
2. **Non-deletion request** ("copy of my data, keep the account"): must return `NOT_APPLICABLE` with `[]`.
3. **Temporal cut-off**: evidence recorded/captured after `audit_at` must neither create a finding nor be cited.
4. Extras: step ordering/numbering/empty-evidence rules, multilingual intent rules, scorer sanity, Groq client token tracking + cache (mock transport).

```
.......                                                                  [100%]
7 passed, 2 deselected in 0.06s
```

## 6. Remaining failures on the sample set

14 of 32 rows differ from ground truth. Missing steps by kind: account / VERIFY_RECORD_ABSENT: 12, analytics / VERIFY_RECORD_ANONYMIZED: 5, audit_record / VERIFY_RECORD_PRESENT: 2

* `case_03` status OK; missing 1, extra 0: VERIFY_RECORD_ANONYMIZED analytics_9406
* `case_04` status OK; missing 1, extra 0: VERIFY_RECORD_ABSENT account_004
* `case_18` status OK; missing 2, extra 0: VERIFY_RECORD_ABSENT account_5521; VERIFY_RECORD_PRESENT audit_record_6645
* `case_20` status OK; missing 2, extra 0: VERIFY_RECORD_ABSENT account_5037; VERIFY_RECORD_PRESENT audit_record_5569
* `case_21` status OK; missing 1, extra 0: VERIFY_RECORD_ABSENT account_6440
* `case_22` status OK; missing 1, extra 0: VERIFY_RECORD_ABSENT account_4990
* `case_23` status OK; missing 1, extra 0: VERIFY_RECORD_ABSENT account_410
* `case_25` status OK; missing 2, extra 0: VERIFY_RECORD_ABSENT account_3136; VERIFY_RECORD_ANONYMIZED analytics_1895
* `case_26` status OK; missing 1, extra 0: VERIFY_RECORD_ANONYMIZED analytics_4566
* `case_27` status OK; missing 2, extra 0: VERIFY_RECORD_ABSENT account_3866; VERIFY_RECORD_ANONYMIZED analytics_4574
* `case_28` status OK; missing 2, extra 0: VERIFY_RECORD_ABSENT account_5482; VERIFY_RECORD_ANONYMIZED analytics_6077
* `case_29` status OK; missing 1, extra 0: VERIFY_RECORD_ABSENT account_7012
* `case_31` status OK; missing 1, extra 0: VERIFY_RECORD_ABSENT account_4857
* `case_32` status OK; missing 1, extra 0: VERIFY_RECORD_ABSENT account_8858

Root causes:
* **Unlinked account / anonymised-analytics records.** These records carry no identifier anywhere in the evidence; the only link is the worker event in `system_activity.csv`, which is shared by several cases with the same `reported_at`. A link is created only when it is unambiguous (one unclaimed candidate event, one unlinked case). Ambiguous groups are left out rather than guessed - this costs recall, never precision.
* **Resources absent from every artefact** (e.g. `account_004`, `audit_record_6645`, `audit_record_5569`): not derivable without inventing evidence.

## 7. Failures found during development and the fixes

| # | Failure found | Fix |
| --- | --- | --- |
| 1 | "remaining copy of my profile" matched the non-deletion cue "copy of" -> wrongly `NOT_APPLICABLE` | cue narrowed to "send me / need a copy of" |
| 2 | "I'm deleting my account" and follow-up wording ("confirm the profile is gone") not matched as deletion | `delet\w*` + default to deletion unless an explicit negative cue exists |
| 3 | Bundles exported *after* `audit_at` were dropped entirely, hiding the state at `audit_at` (COMPLETE cases reported INCOMPLETE) | state is taken from observation time; only *citations* are restricted to files captured <= `audit_at` |
| 4 | Support-case PDF (delivery correction attachment) was never linked | PDF text -> resource/identifier observations |
| 5 | Account convention (`account_<user#>`) produced decoys (`account_050`, `account_541`) | accepted only if the worker delete event is within 30 min of `reported_at` |
| 6 | Activity-window analytics link produced false positives for unfinished cases | only for cases reported `COMPLETE`, and only when unambiguous |
| 7 | Multi-row console screenshots linked every row to every e-mail on screen | OCR identifiers are paired per text line |
| 8 | Test `custom_case_3` found that a record observed after `audit_at` still created a link | links require observation time inside [requested_at, audit_at] |
| 9 | Observations before `requested_at` (old, already-deleted account of a previous lifecycle) polluted the plan | pre-request observations are ignored |

## 8. Method notes

* Deterministic, rule-driven engine. The engine never reads `sample_output.csv` at run time.
* **Disclosure - tuning bias:** the timing windows used to link unlabelled accounts and anonymised analytics profiles (account delete at -80..-35 min and 30 min of `reported_at` for the `account_<user#>` convention; analytics anonymise at -31..-26 and -10..-1 min) were chosen by inspecting the timing of the 32 sample ground-truth rows. The sample scores above are therefore optimistic and are not an unbiased estimate of hidden-grader accuracy.
* The `account_<user#>` naming convention was also inferred from the sample rows.
* Every finding cites a `file_id` captured at or before `audit_at`; remediation and post-remediation checks of INCOMPLETE cases carry an empty `evidence_id`.
* Reported statuses (`COMPLETE` / `PARTIALLY_COMPLETE` / `NOT_STARTED`) and activity `reported_result` are never trusted as proof.
* Hidden-grader accuracy is unknown. Expect lower recall on cases whose accounts could not be linked unambiguously (see section 6).
