import csv, json, sys
from pathlib import Path
import pytest
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

RULES = [("rule_01","USER_PROFILE","Always","DELETE"),("rule_02","SECONDARY_IDENTIFIER","Always","DELETE"),
 ("rule_03","SEARCH_INDEX_ENTRY","Always","DELETE"),("rule_04","CACHE_ENTRY","Always","DELETE"),
 ("rule_05","SUPPORT_MESSAGE","shared","REDACT"),("rule_06","SUPPORT_ATTACHMENT","only subject","DELETE"),
 ("rule_07","ANALYTICS_PROFILE","attributable","ANONYMIZE"),("rule_08","PRODUCT_TELEMETRY","attributable","ANONYMIZE"),
 ("rule_09","USER_EXPORT","Always","DELETE"),("rule_10","MARKETING_PROFILE","Always","DELETE"),
 ("rule_11","EMAIL_DELIVERY_LOG","email","REDACT"),("rule_12","SECURITY_AUDIT_LOG","retention","RETAIN"),
 ("rule_13","LEGAL_HOLD_RECORD","hold","RETAIN"),("rule_14","BACKUP_ARCHIVE","blockable","RETAIN"),
 ("rule_15","ACCOUNT_TOMBSTONE","tombstone","REDACT")]


def _w(path, header, rows):
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh); w.writerow(header); w.writerows(rows)


@pytest.fixture
def mini(tmp_path):
    """Synthetic dataset written from scratch (no real data, no organiser files)."""
    d = tmp_path / "ds"; (d / "evidence" / "records").mkdir(parents=True)
    _w(d / "deletion_rules.csv", ["rule_id","data_category","condition","required_action"], RULES)
    _w(d / "user_identities.csv", ["user_id","name","email","phone","email_hash","updated_at"],
       [("user_900","Ada Test","ada.new@example.com","+1 555 0100","h"*64,"2026-01-01T00:00:00Z"),
        ("user_901","Bo Test","bo@example.com","+1 555 0101","g"*64,"2026-01-01T00:00:00Z")])
    _w(d / "user_audit_log.csv", ["audit_id","user_id","event_time","field_name","old_value","new_value"],
       [("audit_x1","user_900","2026-01-02T00:00:00Z","email","ada.old@example.com","ada.new@example.com")])
    _w(d / "system_activity.csv", ["activity_id","event_time","event_type","source_system","target_system","resource_id","reported_result","details"], [])
    recs = [
      {"recorded_at":"2026-03-02T10:00:00Z","system":"account_service","resource_id":"account_9001","record_type":"USER_PROFILE","result":"FOUND","subject_reference":"ada.old@example.com"},
      {"recorded_at":"2026-03-02T10:00:00Z","system":"identity_service","resource_id":"tombstone_9001","record_type":"ACCOUNT_TOMBSTONE","result":"IDENTIFIABLE","subject_reference":"ada.new@example.com"},
      {"recorded_at":"2026-03-02T10:00:00Z","system":"security_service","resource_id":"audit_record_9001","record_type":"SECURITY_AUDIT_LOG","result":"FOUND","subject_reference":"user_900"},
      {"recorded_at":"2026-03-02T10:00:00Z","system":"backup_service","resource_id":"backup_9001","record_type":"BACKUP_ARCHIVE","result":"ACTIVE","subject_reference":"ada.new@example.com"},
      {"recorded_at":"2026-03-02T10:00:00Z","system":"search_service","resource_id":"search_9001","record_type":"SEARCH_INDEX_ENTRY","result":"FOUND","subject_reference":"decoy@example.com"},
    ]
    (d / "evidence" / "records" / "ledger.json").write_text(json.dumps({"exported_at":"2026-03-02T10:00:00Z","origin":"t","record_count":5,"records":recs}))
    late = [{"recorded_at":"2026-03-09T10:00:00Z","system":"account_service","resource_id":"account_9002","record_type":"USER_PROFILE","result":"FOUND","subject_reference":"bo@example.com"}]
    (d / "evidence" / "records" / "late.json").write_text(json.dumps({"exported_at":"2026-03-09T10:00:00Z","origin":"t","record_count":1,"records":late}))
    _w(d / "evidence_files.csv", ["file_id","file_path","origin","captured_at"],
       [("evidence_01","evidence/records/ledger.json","t","2026-03-02T10:00:00Z"),
        ("evidence_02","evidence/records/late.json","t","2026-03-09T10:00:00Z")])
    hdr = ["case_id","email","requested_at","reported_status","reported_at","audit_at","request_text"]
    _w(d / "deletion_requests.csv", hdr, [
      ("case_a","ada.old@example.com","2026-03-01T00:00:00Z","COMPLETE","2026-03-01T12:00:00Z","2026-03-05T00:00:00Z","Please delete my account and personal data."),
      ("case_b","bo@example.com","2026-03-01T00:00:00Z","COMPLETE","2026-03-01T12:00:00Z","2026-03-05T00:00:00Z","I only want a copy of my data. Please keep the account as it is."),
      ("case_c","bo@example.com","2026-03-01T00:00:00Z","NOT_STARTED","2026-03-01T12:00:00Z","2026-03-05T00:00:00Z","Please erase my account.")])
    _w(d / "sample_deletion_requests.csv", hdr, [])
    return d
