import os

import streamlit as st
from dotenv import load_dotenv

from app.extract import extract_all
from app.merge import Invalid, merge_all
from app.narrative import write_narrative
from app.overrides import apply_overrides, normalize_override
from app.parsers import build_inventory, count_csv_rows, detect_loss_runs, sort_files
from app.rules import evaluate

load_dotenv()
try:  # when deployed, the key can come from Streamlit secrets instead of .env
    if "ANTHROPIC_API_KEY" in st.secrets and not os.environ.get("ANTHROPIC_API_KEY"):
        os.environ["ANTHROPIC_API_KEY"] = st.secrets["ANTHROPIC_API_KEY"]
except Exception:
    pass

RESULT_KEYS = ["inventory", "extractions", "merged", "narrative", "overrides"]


def clear_packet() -> None:
    """Remove everything from the previous packet."""
    for key in RESULT_KEYS:
        st.session_state.pop(key, None)


def process_packet(uploads, pasted_email: str) -> None:
    """Run the fixed pipeline on one packet and store the results in session state."""
    files = sort_files([(u.name, u.getvalue()) for u in uploads], pasted_email=pasted_email)
    inventory = build_inventory(files)
    extractions = extract_all(files, inventory)
    csv_count = next((count_csv_rows(f.name, f.data) for f in files if f.kind == "csv"), None)
    merged = merge_all(extractions, detect_loss_runs(inventory), csv_count)
    st.session_state.inventory = inventory
    st.session_state.extractions = extractions
    st.session_state.merged = merged
    st.session_state.narrative = write_narrative(merged, evaluate(merged))
    st.session_state.overrides = {}
    st.session_state.run_id = st.session_state.get("run_id", 0) + 1


st.set_page_config(page_title="Submission review", layout="wide")
st.title("Commercial auto submission review")

# --- input ---
packet_no = st.session_state.setdefault("packet_no", 0)
uploads = st.file_uploader("Upload one submission packet's files", accept_multiple_files=True, key=f"files_{packet_no}")
pasted = st.text_area("Or paste the broker email", key=f"paste_{packet_no}", height=120)

col_process, col_new = st.columns([1, 5])
if col_process.button("Process packet", type="primary"):
    clear_packet()
    if not uploads and not pasted.strip():
        st.error("Upload the packet's files or paste the broker email first.")
    else:
        with st.status("Processing packet...", expanded=False) as status:
            try:
                process_packet(uploads or [], pasted)
                status.update(label="Processing finished", state="complete")
            except Exception as exc:
                status.update(label="Processing failed", state="error")
                st.error(f"Processing failed: {exc}")
if col_new.button("Start new packet"):
    clear_packet()
    st.session_state.packet_no += 1  # gives the upload and paste boxes fresh, empty keys
    st.rerun()

if "merged" not in st.session_state:
    st.stop()

# --- processing messages ---
llm_errors = [e for e in st.session_state.extractions if e.error]
for e in llm_errors:
    st.error(f"{e.source_file}: {e.error}")
for entry in st.session_state.inventory:
    if not entry.readable:
        st.warning(f"{entry.name}: {entry.note}")
if not llm_errors:
    st.success("Packet processed.")

effective = apply_overrides(st.session_state.merged, st.session_state.overrides)
evaluation = evaluate(effective)

# --- rule results ---
st.header(f"Overall: {evaluation.overall}")
st.caption("Readiness for human review only. Not an approval, decline, quote or binding decision.")
st.dataframe(
    [
        {
            "Rule": r.rule_id,
            "Name": r.name,
            "Status": r.status,
            "Reason": r.reason,
            "Values used": ", ".join(f"{k} = {v!r} ({r.states[k]})" for k, v in r.values.items()),
        }
        for r in evaluation.rules
    ],
    hide_index=True,
    width="stretch",
)

# --- fields with evidence ---
st.header("Extracted fields")
for field in effective.values():
    label = f"{field.name}: {field.value!r} [{field.state}]"
    if field.human_override:
        label += " - HUMAN OVERRIDE"
    with st.expander(label):
        if field.human_override:
            st.write(f"Original extracted value: {field.original_value!r} [{field.original_state}]")
        if field.note:
            st.write(field.note)
        st.write("Sources checked: " + (", ".join(field.checked_sources) or "none"))
        for ev in field.evidence:
            st.markdown(f"**{ev.source_file}** - {ev.locator or 'location not found'}")
            st.code(ev.excerpt or "(no excerpt)", language=None)

# --- attachment inventory ---
st.header("Attachment inventory")
st.dataframe(
    [
        {"File": e.name, "Kind": e.kind, "Document type": e.doc_type, "Readable": e.readable, "Note": e.note}
        for e in st.session_state.inventory
    ],
    hide_index=True,
    width="stretch",
)

# --- corrections ---
st.header("Correct a field")
with st.form("override_form", clear_on_submit=True):
    field_name = st.selectbox("Field", list(effective))
    new_value = st.text_input("Corrected value")
    if st.form_submit_button("Apply correction and rerun checks"):
        try:
            st.session_state.overrides[field_name] = normalize_override(field_name, new_value)
            st.rerun()
        except Invalid as reason:
            st.error(f"Not applied: {reason}")
if st.session_state.overrides:
    st.write("Human overrides: " + ", ".join(f"{k} = {v!r}" for k, v in st.session_state.overrides.items()))
    if st.button("Remove all corrections"):
        st.session_state.overrides = {}
        st.rerun()

# --- AI summary and draft email ---
narrative = st.session_state.narrative
st.header("Underwriter summary (AI-generated)")
if narrative.error:
    st.error(narrative.error)
else:
    st.write(narrative.summary)

st.header("Draft broker email")
st.caption("Draft only. This app does not send email.")
if narrative.error:
    st.error(narrative.error)
else:
    st.text_area("Edit the draft", value=narrative.email_draft, height=320, key=f"email_{st.session_state.run_id}")
