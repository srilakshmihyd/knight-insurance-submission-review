# Commercial Auto Submission Review

A small local web app that helps an underwriter review a commercial auto submission. It reads the
uploaded packet, extracts the required fields with source evidence, runs checks R1–R5 in plain
Python, and shows an AI-written summary and an editable draft email to the broker.

## Setup and run

Requires Python 3.11.

```bash
python3.11 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env        # then put your Anthropic API key in .env
streamlit run streamlit_app.py
```

The app opens at http://localhost:8501.

`.env` settings:

| Variable | Meaning |
|---|---|
| `ANTHROPIC_API_KEY` | Anthropic API key. Required for extraction and the summary. |
| `ANTHROPIC_MODEL` | Optional. Defaults to `claude-sonnet-5-5`. |

`.env` is gitignored, and no key is stored in the code. If the key is missing or invalid, the app
shows "LLM unavailable" and still shows every result that does not need the LLM: the attachment
inventory, the CSV vehicle count, loss runs detection and the rule results that follow from them.

## Using the app

1. Upload all files from one packet (for example everything in `sample_submissions/SUB-A`), or
   paste the broker email into the text box.
2. Click **Process packet**.
3. Review the overall status, the rule results, the **Needs attention** list, each field with its
   evidence (fields that are not KNOWN open automatically), and the attachment inventory.
4. To correct a field, choose it under **Correct a field**, type the value and apply. The checks
   rerun straight away. The field is labelled **HUMAN OVERRIDE**, and its original value and
   evidence are kept.
5. After a correction, a notice shows that the summary and draft email are out of date. Click
   **Regenerate summary and email** to rewrite them with the corrected values.
6. Edit the draft broker email as needed. The app never sends email.
7. Click **Start new packet** before loading another submission. This clears all data from the
   previous packet.

## Tests

```bash
pytest
```

Result: 112 passed, 8 skipped. The skipped tests are the live LLM tests described below. The
normal run never calls the live LLM; LLM calls are replaced with stubs or a fake client. The tests cover:

- missing information (MISSING fields, R1 FLAG, the list of sources checked);
- a source conflict (email and PDF disagree, so the value is null and both answers are kept);
- unequal vehicle counts (R2 FLAG) and zero vehicles as a valid count;
- the 150-mile threshold (150 passes and 150.1 is flagged);
- loss runs detection (a readable report with zero claims, other report titles such as "Claims
  history", an email that only promises a report, and an unreadable file);
- normalization of harmless differences (state names such as "Texas", dates such as "Nov 1, 2026",
  "150 miles", "4 vehicles"), while hedged answers such as "about 150" stay INVALID;
- the grounding check (an answer missing from its own excerpt, or an excerpt not found in the
  source, is INVALID);
- human overrides (a correction is labelled, the original is kept, and the checks rerun);
- LLM failures: a missing key, a connection error, or an answer that does not match the schema
  each show "LLM unavailable", and the results that come from files and the rules are kept;
- the Streamlit screen, run with Streamlit's AppTest: an empty start screen, processing, the error
  and success messages, corrections, the out-of-date notice and **Regenerate summary and email**
  after a correction, and "Start new packet" clearing everything.

Live LLM tests call the real API, so they run only when asked for:

```bash
RUN_LIVE_TESTS=1 pytest tests/test_live.py
```

Result: 8 passed. They check the three sample packets against the expected outcomes, a conflict
keeping both values, "Not provided" becoming MISSING, instructions inside a document being
ignored, the summary and draft being written, and an invalid key showing "LLM unavailable".

Results on the sample packets match the expected outcomes:

| Packet | R1 | R2 | R3 | R4 | R5 | Overall |
|---|---|---|---|---|---|---|
| SUB-A | PASS | PASS | PASS | PASS | PASS | READY_FOR_REVIEW |
| SUB-B | FLAG | PASS | UNKNOWN | UNKNOWN | FLAG | NEEDS_REVIEW |
| SUB-C | PASS | FLAG | UNKNOWN | UNKNOWN | FLAG | NEEDS_REVIEW |

## LLM provider and model

Anthropic Claude through the official `anthropic` Python SDK. The default model is `claude-sonnet-5-5`.

The LLM is used for two things:

1. **Extraction.** One call for the broker email and one for the application PDF. The answer must
   match a JSON schema and is validated with Pydantic.
2. **Narrative.** The underwriter summary and the draft broker email.

The LLM never decides PASS, FLAG or UNKNOWN.

## Architecture

The app runs a fixed pipeline; it is not an agent.

```
upload
 -> parsers.py    PDF text per page, CSV row count, attachment inventory,
                  PDF type from its page 1 heading, loss runs detection
 -> extract.py    LLM extraction from the email and the application PDF
                  (value as written, plus the exact excerpt; the location is found in code)
 -> merge.py      normalize values and assign KNOWN / MISSING / CONFLICT / INVALID
 -> rules.py      R1-R5 and the overall status, in plain Python
 -> narrative.py  LLM summary and draft broker email
 -> overrides.py  human corrections, stored separately; the checks rerun
```

| File | Purpose |
|---|---|
| `streamlit_app.py` | The browser interface and session state (cleared for each packet) |
| `app/models.py` | Pydantic data models |
| `app/parsers.py` | Reading files, the attachment inventory and loss runs detection (no LLM) |
| `app/extract.py` | LLM extraction from the email and the application PDF |
| `app/merge.py` | Combining sources, normalization and field states |
| `app/rules.py` | R1–R5 and the overall status |
| `app/narrative.py` | LLM summary and draft email. The items to request are chosen in code. |
| `app/overrides.py` | Human overrides |
| `tests/` | pytest tests that need no live LLM |

How the main requirements are met:

- **Evidence.** Each value shows its source file, an exact excerpt, and a location ("email body"
  or "PDF page N"). The CSV count shows its data-row range. A missing field lists the sources
  that were checked. A missing loss runs report shows the attachment inventory.
- **Conflicts.** When the email and PDF give different values, the field becomes CONFLICT, its
  value is null, and both answers are shown with their evidence. No source is preferred.
- **Loss runs** are decided only from the uploaded files, never from the email text.
- **Each packet is independent.** Processing a packet or starting a new one clears all previous
  results, overrides and drafts.

## AI coding tools used

- Claude Code (Anthropic)

## Time spent

6 hours.

## Known limitations
- **Accepted formats.** Common forms are cleaned up in code: any US state name or code
  ("Texas" -> TX); dates such as "11/01/2026", "Nov 1, 2026" or "1 November 2026"; numbers with a
  unit or commas ("150 miles", "1,200 mi", "4 vehicles", "four"). Slashed dates are read
  month/day/year. Hedged answers ("about 150", "under 100") stay INVALID on purpose.
- **Grounding check.** An answer is used only if its quoted excerpt is found in the source text
  and the answer appears word for word in that excerpt; otherwise the field is INVALID.
- **Loss runs** are recognised by the page 1 heading (e.g. "Loss runs", "Loss history",
  "Claims history", "Loss experience"). A report with an unusual title is not detected, and R5 FLAGs.
- **Operations description** is compared as text, ignoring case and a final period. Different
  wording in the email and the PDF is shown as CONFLICT. No rule uses this field.
- **If the LLM fails for every source,** the extracted fields cannot be checked, so R1 gives
  UNKNOWN rather than FLAG.
- **After a correction** the checks rerun straight away. The summary and draft email are marked as
  out of date until the reviewer clicks "Regenerate summary and email" (one more LLM call).
  Regenerating replaces any edits made to the draft.

## Incomplete

Nothing in the required flow is known to be incomplete.
