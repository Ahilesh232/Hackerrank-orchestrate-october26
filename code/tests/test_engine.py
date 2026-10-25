import json
from pathlib import Path
from ghostaudit.engine import AuditEngine, load_cases, steps_to_json
from ghostaudit.intent import classify_request
from ghostaudit.evaluate import score
from ghostaudit.llm import GroqClient

def run(mini):
    eng = AuditEngine(mini, use_ocr=False)
    cases = load_cases(mini / "deletion_requests.csv"); eng.prepare(cases)
    return {c.case_id: eng.audit(c) for c in cases}

def test_custom_case_1_historical_email_tombstone_backup(mini):
    r = run(mini)["case_a"]
    assert r.identity.user_id == "user_900" and r.identity.matched_via == "historical_email"
    assert r.status == "INCOMPLETE"
    ops = {(s["operation"], s["resource_id"]) for s in r.steps}
    assert ("DELETE_RECORD", "account_9001") in ops
    assert ("REDACT_RECORD", "tombstone_9001") in ops          # tombstone: keep reference, strip identifiers
    assert ("BLOCK_RESTORE", "backup_9001") in ops             # backups retained, restore blocked
    assert ("VERIFY_RECORD_PRESENT", "audit_record_9001") in ops   # retained audit record
    assert not any(s["resource_id"] == "search_9001" for s in r.steps)   # decoy owned by someone else

def test_custom_case_2_non_deletion_is_not_applicable(mini):
    r = run(mini)["case_b"]
    assert r.status == "NOT_APPLICABLE" and r.steps == []

def test_custom_case_3_evidence_after_audit_cut_off_ignored(mini):
    r = run(mini)["case_c"]
    assert all(s["resource_id"] != "account_9002" for s in r.steps)
    assert all(s["evidence_id"] != "evidence_02" for s in r.steps)

def test_step_ordering_and_numbering(mini):
    r = run(mini)["case_a"]
    assert [s["step_no"] for s in r.steps] == list(range(1, len(r.steps) + 1))
    phases = [s["phase"] for s in r.steps]
    assert phases == sorted(phases, key=["FINDING", "REMEDIATION", "VERIFICATION"].index)
    for s in r.steps:
        if s["phase"] != "FINDING":
            assert s["evidence_id"] == ""

def test_intent_rules():
    assert classify_request("Je demande la suppression définitive du compte.")["authorizes_deletion"]
    assert classify_request("한국어 삭제 요청 삭제")["authorizes_deletion"]
    assert not classify_request("Please remove me from marketing lists only.")["authorizes_deletion"]
    assert not classify_request("Do not close the account.")["authorizes_deletion"]
    assert classify_request("Please remove any remaining copy of my profile.")["authorizes_deletion"]

def test_scoring_perfect_and_wrong():
    g = {"c": ("COMPLETE", [{"step_no":1,"phase":"VERIFICATION","system_id":"a","resource_id":"r","operation":"VERIFY_RECORD_ABSENT","state":"ABSENT","evidence_id":"e"}])}
    assert score(g, g)["row_exact_match"] == 1.0
    assert score({"c": ("INCOMPLETE", [])}, g)["status_accuracy"] == 0.0

def test_groq_client_tracks_usage_and_caches(tmp_path):
    calls = []
    def fake(payload):
        calls.append(1)
        return {"choices": [{"message": {"content": '{"authorizes_deletion": false, "reason": "x"}'}}],
                "usage": {"prompt_tokens": 100, "completion_tokens": 10}}
    c = GroqClient(api_key="k", cache_dir=tmp_path, transport=fake)
    fb = {"authorizes_deletion": True, "reason": "", "ambiguous": True, "method": "rules", "confidence": .5}
    assert c.classify_intent("hello", fb)["authorizes_deletion"] is False
    c.classify_intent("hello", fb)                       # served from cache
    s = c.usage_summary()
    assert len(calls) == 1 and s["calls"] == 1 and s["input_tokens"] == 100 and s["output_tokens"] == 10
