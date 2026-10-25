"""Streamlit UI:  streamlit run code/app.py"""
from __future__ import annotations


import io
import json
import os
import sys
import tempfile
import zipfile
from collections import Counter
from pathlib import Path

import pandas as pd
import streamlit as st

HERE = Path(__file__).resolve().parent

try:  # optional: read GROQ_API_KEY etc. from code/.env
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).resolve().parent / ".env")
except ImportError:
    pass

sys.path.insert(0, str(HERE))

from ghostaudit.engine import AuditEngine, load_cases, steps_to_json  # noqa: E402
from ghostaudit.evaluate import read_output, score  # noqa: E402
from ghostaudit.intent import classify_request  # noqa: E402
from ghostaudit.validate import validate_output  # noqa: E402

st.set_page_config(page_title="Ghost Data Audit", page_icon="🔎", layout="wide")
st.markdown("""<style>.block-container{padding-top:1.5rem}
.pill{display:inline-block;padding:2px 10px;border-radius:12px;font-size:.8rem;font-weight:600}
.COMPLETE{background:#d1fae5;color:#065f46}.INCOMPLETE{background:#fee2e2;color:#991b1b}
.NOT_APPLICABLE{background:#e5e7eb;color:#374151}</style>""", unsafe_allow_html=True)


# ------------------------------------------------------------------ helpers
def find_dataset_root(base: Path) -> Path | None:
    for p in [base, *base.rglob("*")]:
        if p.is_dir() and (p / "evidence_files.csv").exists():
            return p
    return None


@st.cache_resource(show_spinner="Parsing evidence (SQL, JSON, logs, PDFs, images)...")
def get_engine(path: str, use_ocr: bool):
    return AuditEngine(Path(path), use_ocr=use_ocr)


def pill(status: str) -> str:
    return f'<span class="pill {status}">{status}</span>'


def result_rows(results):
    return pd.DataFrame([{
        "case_id": r.case.case_id, "email": r.case.email, "reported": r.case.reported_status,
        "audit_status": r.status, "user_id": r.identity.user_id if r.identity else "",
        "agent": r.intent.get("method", "rule"),
        "findings": sum(s["phase"] == "FINDING" for s in r.steps),
        "remediation": sum(s["phase"] == "REMEDIATION" for s in r.steps),
        "verification": sum(s["phase"] == "VERIFICATION" for s in r.steps),
        "intent_reason": r.intent.get("reason", ""), } for r in results])


def build_csv(results) -> bytes:
    buf = io.StringIO()
    import csv
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(["case_id", "deletion_status", "steps_json"])
    for r in sorted(results, key=lambda r: r.case.case_id):
        w.writerow([r.case.case_id, r.status, steps_to_json(r.steps)])
    return buf.getvalue().encode()


def build_zip(results, usage) -> bytes:
    mem = io.BytesIO()
    with zipfile.ZipFile(mem, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("output.csv", build_csv(results))
        z.writestr("summary.json", json.dumps(dict(Counter(r.status for r in results)), indent=2))
        for r in results:
            z.writestr(f"cases/{r.case.case_id}.json", json.dumps({
                "case_id": r.case.case_id, "status": r.status, "intent": r.intent,
                "user_id": r.identity.user_id if r.identity else None,
                "links": {k: {"tier": v.tier, "note": v.note} for k, v in r.links.items()},
                "steps": r.steps}, indent=2))
        if usage:
            z.writestr("usage.json", json.dumps(usage, indent=2))
    return mem.getvalue()


# ------------------------------------------------------------------ sidebar
st.sidebar.title("🔎 Ghost Data Audit")
src = st.sidebar.radio("Dataset source", ["Folder path", "Upload ZIP"])
dataset_dir = None
default_path = str(HERE.parent / "dataset")
if src == "Folder path":
    p = st.sidebar.text_input("Dataset folder", default_path)
    if Path(p).exists():
        dataset_dir = find_dataset_root(Path(p))
else:
    up = st.sidebar.file_uploader("Repository or dataset ZIP", type="zip")
    if up is not None:
        tmp = Path(st.session_state.setdefault("_tmp", tempfile.mkdtemp()))
        if st.session_state.get("_upname") != up.name:
            with zipfile.ZipFile(up) as z:
                z.extractall(tmp)
            st.session_state["_upname"] = up.name
        dataset_dir = find_dataset_root(tmp)
if dataset_dir is None:
    st.sidebar.error("No dataset found (needs evidence_files.csv).")
    st.title("Ghost Data Still Exists - deletion audit agent")
    st.info("Select a dataset folder or upload a ZIP in the sidebar.")
    st.stop()
st.sidebar.success(f"Dataset: {dataset_dir}")

st.sidebar.markdown("---")
st.sidebar.subheader("📄 Requests File")
uploaded_req_file = st.sidebar.file_uploader(
    "Upload requests CSV",
    type=["csv"],
    help="Upload your deletion requests CSV (e.g. deletion_requests.csv or sample_deletion_requests.csv)",
)

req_file_path = None
req_file_name = None
if uploaded_req_file is not None:
    tmp_req_dir = Path(st.session_state.setdefault("_tmp_reqs", tempfile.mkdtemp()))
    req_file_path = tmp_req_dir / uploaded_req_file.name
    req_file_path.write_bytes(uploaded_req_file.getvalue())
    req_file_name = uploaded_req_file.name
    st.sidebar.success(f"Uploaded: `{req_file_name}`")
else:
    st.sidebar.info("Upload a requests CSV file to run audit.")

api_key = os.environ.get("GROQ_API_KEY", "").strip()

st.sidebar.markdown("---")
st.sidebar.subheader("🤖 Agent Configuration")
agent_mode = st.sidebar.radio(
    "Active Agents",
    [
        "Both (Rule-Based Agent + Groq AI)",
        "Rule-Based Agent Only",
        "Groq AI Agent Only",
    ],
    index=0,
    help="Both: Rule-based deterministic engine enhanced with Groq AI for ambiguous intent classification."
)

use_llm = agent_mode in ("Both (Rule-Based Agent + Groq AI)", "Groq AI Agent Only")
if use_llm:
    default_text_model = os.environ.get("GROQ_TEXT_MODEL", "openai/gpt-oss-20b")
    text_model = st.sidebar.text_input("Groq Model", default_text_model)
    if api_key:
        st.sidebar.caption("🔒 **Groq API**: Active & Secured (Key hidden from UI)")
    else:
        st.sidebar.error("⚠️ GROQ_API_KEY missing from environment/.env")
else:
    st.sidebar.caption("⚡ **Rule-Based Agent**: Active (Deterministic rules only)")
    text_model = ""

st.sidebar.markdown("---")
st.sidebar.subheader("⚙️ Audit Options")
use_ocr = st.sidebar.checkbox("OCR screenshots (Tesseract)", value=True)
limit = st.sidebar.slider("Max cases", 1, 600, 600)
st.sidebar.caption("Rows beyond the file length are ignored.")
run = st.sidebar.button("▶ Run audit", type="primary", disabled=(req_file_path is None))

if run:
    if req_file_path is None:
        st.sidebar.error("Please upload a requests CSV file.")
        st.stop()
    eng = get_engine(str(dataset_dir), use_ocr)
    cases = load_cases(req_file_path)
    if not cases:
        st.sidebar.error("The uploaded CSV has no valid cases.")
        st.stop()
    pool = list(cases)
    for extra_name in ("sample_deletion_requests.csv", "deletion_requests.csv"):
        extra_p = dataset_dir / extra_name
        if extra_p.exists() and extra_p.resolve() != req_file_path.resolve():
            pool.extend(load_cases(extra_p))
    eng.prepare(pool)
    llm = None
    if use_llm:
        if not api_key:
            st.sidebar.error("GROQ_API_KEY is not configured in .env or environment.")
            st.stop()
        from ghostaudit.llm import GroqClient
        llm = GroqClient(api_key=api_key, text_model=text_model)
    bar = st.progress(0.0, text="Auditing...")
    results = []
    sel = cases[:limit]
    for i, c in enumerate(sel, 1):
        rule_intent = classify_request(c.request_text)
        if agent_mode == "Rule-Based Agent Only":
            final_intent = rule_intent
        elif agent_mode == "Groq AI Agent Only":
            final_intent = llm.classify_intent(c.request_text, fallback=rule_intent) if llm else rule_intent
        else:  # Both (Rule-Based + Groq AI)
            if rule_intent["ambiguous"] and llm:
                final_intent = llm.classify_intent(c.request_text, fallback=rule_intent)
            else:
                final_intent = rule_intent
        results.append(eng.audit(c, final_intent))
        bar.progress(i / len(sel), text=f"Auditing {c.case_id} ({i}/{len(sel)})")
    bar.empty()
    st.session_state.update(results=results, engine=eng, req_file=req_file_name, dataset_dir=str(dataset_dir),
                            usage=llm.usage_summary() if llm else None, agent_mode=agent_mode)

st.title("Ghost Data Still Exists - deletion audit agent")
if "results" not in st.session_state:
    if req_file_path is None:
        st.info("👈 **Step 1:** Upload a requests CSV file in the sidebar to get started.")
    else:
        st.info(f"Requests file **{req_file_name}** loaded ({len(load_cases(req_file_path))} cases). "
                f"Review configuration in the sidebar and press **▶ Run audit**.")
    st.stop()

results = st.session_state["results"]
eng: AuditEngine = st.session_state["engine"]
usage = st.session_state["usage"]
df = result_rows(results)

tab_over, tab_output, tab_case, tab_eval, tab_usage, tab_ev = st.tabs(
    ["Overview", "Formatted Output (CSV)", "Case inspector", "Evaluation & validation", "Tokens & cost", "Evidence index"])

with tab_over:
    counts = Counter(r.status for r in results)
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Cases", len(results)); c2.metric("COMPLETE", counts["COMPLETE"])
    c3.metric("INCOMPLETE", counts["INCOMPLETE"]); c4.metric("NOT_APPLICABLE", counts["NOT_APPLICABLE"])
    left, right = st.columns([1, 2])
    left.bar_chart(pd.Series(counts, name="cases"))
    f = right.multiselect("Filter status", ["COMPLETE", "INCOMPLETE", "NOT_APPLICABLE"],
                          ["COMPLETE", "INCOMPLETE", "NOT_APPLICABLE"])
    rep = right.multiselect("Reported status", sorted(df.reported.unique()), sorted(df.reported.unique()))
    q = right.text_input("Search case id / email")
    view = df[df.audit_status.isin(f) & df.reported.isin(rep)]
    if q:
        view = view[view.case_id.str.contains(q, case=False) | view.email.str.contains(q, case=False)]
    st.dataframe(view, width="stretch", hide_index=True)
    mism = df[(df.reported == "COMPLETE") & (df.audit_status == "INCOMPLETE")]
    st.caption(f"{len(mism)} cases were reported COMPLETE by the workflow but the audit found remaining data.")
    d1, d2 = st.columns(2)
    d1.download_button("⬇ output.csv", build_csv(results), "output.csv", "text/csv")
    d2.download_button("⬇ results.zip (csv + per-case JSON)", build_zip(results, usage), "audit_results.zip",
                       "application/zip")

with tab_output:
    st.subheader("📋 Output (Format: case_id, deletion_status, steps_json)")
    csv_bytes = build_csv(results)
    csv_str = csv_bytes.decode("utf-8")
    col1, col2 = st.columns([1, 3])
    col1.download_button("⬇ Download output.csv", csv_bytes, "output.csv", "text/csv", type="primary")
    col2.info(f"**Output Format:** `case_id,deletion_status,steps_json` ({len(results)} cases)")
    st.markdown("### Raw CSV Output")
    st.code(csv_str, language="csv")

with tab_case:
    cid = st.selectbox("Case", [r.case.case_id for r in results])
    r = next(x for x in results if x.case.case_id == cid)
    st.markdown(f"### {cid} &nbsp; {pill(r.status)}", unsafe_allow_html=True)
    a, b = st.columns(2)
    with a:
        st.write("**Request**", r.case.email)
        st.write(r.case.request_text)
        st.write(f"requested `{r.case.requested_at:%Y-%m-%d %H:%M}` · reported **{r.case.reported_status}** "
                 f"`{r.case.reported_at:%Y-%m-%d %H:%M}` · audit_at `{r.case.audit_at:%Y-%m-%d %H:%M}`")
        st.write(f"**Intent**: {'deletion' if r.intent['authorizes_deletion'] else 'not a deletion'} "
                 f"({r.intent['method']}, {r.intent['reason']})")
    with b:
        if r.identity:
            i = r.identity
            st.write(f"**Resolved user** `{i.user_id}` ({i.name}) via {i.matched_via}")
            st.write("Current email:", i.current_email)
            st.write("Historical emails:", sorted(i.historical_emails) or "none")
            st.write(f"Phones: {len(i.phones)} · email hashes: {len(i.hashes)}")
        else:
            st.warning("Identity not resolved.")
    if r.links:
        st.subheader("Linked resources")
        st.dataframe(pd.DataFrame([{"resource_id": a_.rid, "system": a_.system, "category": a_.category,
                                    "state_at_audit": a_.state, "violation": a_.violation,
                                    "evidence": a_.evidence_id,
                                    "link_tier": r.links[a_.rid].tier, "link_note": r.links[a_.rid].note}
                                   for a_ in r.assessments]), width="stretch", hide_index=True)
    for phase, title in (("FINDING", "Findings"), ("REMEDIATION", "Remediation plan"),
                         ("VERIFICATION", "Verification checks")):
        rows = [s for s in r.steps if s["phase"] == phase]
        st.subheader(f"{title} ({len(rows)})")
        if rows:
            st.dataframe(pd.DataFrame(rows), width="stretch", hide_index=True)
    st.subheader("steps_json")
    st.code(json.dumps(r.steps, indent=2), language="json")
    cited = sorted({s["evidence_id"] for s in r.steps if s["evidence_id"]})
    if cited:
        st.subheader("Evidence summaries")
        for fid in cited:
            m = eng.store.manifest.get(fid)
            if not m:
                st.write(f"`{fid}` (activity/audit id)")
                continue
            with st.expander(f"{fid} · {m['path']} · captured {m['captured_at']:%Y-%m-%d %H:%M}"):
                rel = [o for o in eng.store.obs if o.file_id == fid and o.rid in r.links]
                st.dataframe(pd.DataFrame([{"resource": o.rid, "state": o.st, "observed": o.t, "origin": o.origin}
                                           for o in rel]), hide_index=True, width="stretch")
                d = eng.store.docs.get(fid)
                if d:
                    st.text(d.text[:1500])

with tab_eval:
    st.subheader("Submission validation")
    with tempfile.NamedTemporaryFile("wb", suffix=".csv", delete=False) as tf:
        tf.write(build_csv(results))
    errs = validate_output(Path(tf.name), Path(st.session_state["dataset_dir"]))
    if not errs:
        st.success("output.csv passes schema, ordering and evidence-date checks")
    else:
        st.error(f"{len(errs)} problems found:")
        for e in errs[:30]:
            st.write(e)
    gold_path = Path(st.session_state["dataset_dir"]) / "sample_output.csv"
    if gold_path.exists():
        gold = read_output(gold_path)
        matching = {r.case.case_id: (r.status, r.steps) for r in results if r.case.case_id in gold}
        if matching:
            st.subheader(f"Accuracy vs sample_output.csv ({len(matching)} matching cases)")
            sc = score(matching, gold)
            m = st.columns(4)
            m[0].metric("Status accuracy", f"{sc['status_accuracy']:.1%}")
            m[1].metric("Step F1", f"{sc['step_f1']:.3f}")
            m[2].metric("Exact rows", f"{sc['row_exact_match']:.1%}")
            m[3].metric("Sequence (LCS)", f"{sc['sequence_lcs_ratio']:.3f}")
            st.write("Field accuracy", pd.Series(sc["field_accuracy"]).round(3).to_frame("accuracy").T)
            bad = {c: v for c, v in sc["per_case"].items() if not v["exact"]}
            if bad:
                st.write(f"{len(bad)} cases differ from ground truth")
                for c, v in bad.items():
                    with st.expander(c):
                        st.write("missing", v["missing"]); st.write("extra", v["extra"])
            else:
                st.success("All matching sample cases matched ground truth exactly!")
        else:
            st.caption("Uploaded cases do not overlap with sample_output.csv case IDs.")
    else:
        st.caption("No sample_output.csv found in dataset folder.")

with tab_usage:
    if usage:
        st.metric("Model calls", usage["calls"])
        c = st.columns(3)
        c[0].metric("Input tokens", usage["input_tokens"]); c[1].metric("Output tokens", usage["output_tokens"])
        c[2].metric("Est. cost (USD)", f"{usage['est_cost_usd']:.6f}")
        st.dataframe(pd.DataFrame(usage["models"]).T)
    else:
        st.info("No LLM calls were made in this run (deterministic engine only): 0 calls, 0 tokens, $0.")

with tab_ev:
    rows = []
    for fid, m in eng.store.manifest.items():
        n = sum(1 for o in eng.store.obs if o.file_id == fid)
        d = eng.store.docs.get(fid)
        rows.append({"file_id": fid, "path": m["path"], "origin": m["origin"], "captured_at": m["captured_at"],
                     "observations": n, "document_chars": len(d.text) if d else 0})
    st.dataframe(pd.DataFrame(rows), width="stretch", hide_index=True)
