"""Integration tests against the real dataset (skipped when it is not present)."""
import csv
from pathlib import Path
import pytest
from ghostaudit.engine import AuditEngine, load_cases
from ghostaudit.evaluate import read_output, score
from ghostaudit.validate import validate_output

DS = Path(__file__).resolve().parents[2] / "dataset"
pytestmark = pytest.mark.skipif(not (DS / "deletion_requests.csv").exists(), reason="dataset not mounted")

def test_sample_accuracy_floor():
    eng = AuditEngine(DS, use_ocr=False)
    cases = load_cases(DS / "sample_deletion_requests.csv")
    eng.prepare(cases + load_cases(DS / "deletion_requests.csv"))
    pred = {}
    for c in cases:
        r = eng.audit(c); pred[c.case_id] = (r.status, r.steps)
    sc = score(pred, read_output(DS / "sample_output.csv"))
    assert sc["status_accuracy"] >= 0.9 and sc["step_precision"] >= 0.95

def test_full_output_validates(tmp_path):
    import main
    out = tmp_path / "output.csv"
    main.run(DS, out)
    assert validate_output(out, DS) == []
    assert len(list(csv.reader(open(out)))) == 519
