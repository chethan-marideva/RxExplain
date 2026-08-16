
from __future__ import annotations

import sys
import time
from pathlib import Path

import streamlit as st

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT / "src") not in sys.path:              # allow running without install
    sys.path.insert(0, str(ROOT / "src"))

from rxexplain import SYSTEMS, __version__                          # noqa: E402
from rxexplain.config import load_azure_config                      # noqa: E402
from rxexplain.drugkb import kb_stats                                # noqa: E402
from rxexplain.evaluate import (                                     # noqa: E402
    abbreviation_leaks,
    fact_coverage,
    load_gold,
    readability,
)
from rxexplain.parser_rules import parse                             # noqa: E402
from rxexplain.safety import check as safety_check                    # noqa: E402
from rxexplain.systems import run_system                              # noqa: E402

LABELS = {
    "rule": "A : Rule based baseline",
    "zeroshot": "B : Zero shot LLM baseline",
    "sota": "C : SOTA grounded pipeline",
}
SEVERITY_STYLE = {
    "critical": ("#7f1d1d", "#fee2e2", "CRITICAL"),
    "warning": ("#92400e", "#fef3c7", "WARNING"),
    "caution": ("#1e40af", "#dbeafe", "CAUTION"),
    "info": ("#374151", "#f3f4f6", "NOTE"),
}

st.set_page_config(
    page_title="LLM Project",
    page_icon="Rx",
    layout="wide",
)


@st.cache_resource(show_spinner=False)
def get_client():
    """One LLM client per session. Returns ``(client, error_message)``."""
    try:
        cfg = load_azure_config(require=False)
    except Exception as exc:
        return None, str(exc)
    if cfg is None:
        return None, "not configured"
    try:
        from rxexplain.llm import LLMClient

        return LLMClient(cfg), None
    except Exception as exc:
        return None, str(exc)


@st.cache_data(show_spinner=False)
def get_gold():
    try:
        return load_gold()
    except Exception:
        return []


def severity_box(severity: str, message: str) -> None:
    fg, bg, label = SEVERITY_STYLE.get(severity, SEVERITY_STYLE["info"])
    st.markdown(
        f"<div style='background:{bg};border-left:5px solid {fg};padding:0.6rem 0.8rem;"
        f"border-radius:4px;margin-bottom:0.5rem;color:#111827'>"
        f"<b style='color:{fg}'>{label}</b><br>{message}</div>",
        unsafe_allow_html=True,
    )


def show_parsed_facts(text: str) -> None:
    parsed = parse(text)
    if not parsed.medications:
        st.info("No medicine lines were recognised in this text.")
        return
    rows = []
    for med in parsed.medications:
        rows.append({
            "As written": med.name or "-",
            "Generic": med.generic or "not in knowledge base",
            "Strength": med.strength or med.dose_amount or "-",
            "Schedule": med.frequency_human or "NOT STATED",
            "Timing": med.timing or "-",
            "Duration": med.duration or "NOT STATED",
            "Only if needed": "yes" if med.prn else "no",
        })
    st.dataframe(rows, use_container_width=True, hide_index=True)
    c1, c2, c3 = st.columns(3)
    c1.metric("Lines parsed", f"{len(parsed.medications)}")
    c2.metric("Parse coverage", f"{parsed.coverage:.0%}")
    c3.metric("Known to KB", f"{sum(1 for m in parsed.medications if m.generic)}")
    if parsed.general_instructions:
        st.caption("Other instructions kept: " + "; ".join(parsed.general_instructions))
    if parsed.unparsed_lines:
        st.warning("Could not read: " + " | ".join(parsed.unparsed_lines))


def show_quality(text: str, gold_case=None) -> None:
    read = readability(text)
    leaks = abbreviation_leaks(text)
    cols = st.columns(4)
    cols[0].metric("Reading grade", f"{read['fk_grade']:.1f}")
    cols[1].metric("SMOG", f"{read['smog']:.1f}")
    cols[2].metric("Words", read["words"])
    cols[3].metric(
        "Abbreviations left",
        leaks["leak_count"],
        delta="clean" if leaks["clean"] else ", ".join(leaks["codes"][:4]),
        delta_color="normal" if leaks["clean"] else "inverse",
    )
    if gold_case is not None:
        cov = fact_coverage(text, gold_case.must_include)
        st.metric(
            "Critical facts covered",
            f"{cov['hits']}/{cov['total']}  ({cov['coverage']:.0%})",
        )
        if cov["missing"]:
            st.caption("Missing: " + "; ".join(cov["missing"]))


def render_result(exp, gold_case=None) -> None:
    if exp.error:
        st.error(exp.error)
    if not exp.output_text:
        return
    st.text(exp.output_text)

    with st.expander("Quality metrics for this output", expanded=False):
        show_quality(exp.output_text, gold_case)

    foot = (
        f"{exp.latency_s:.2f}s | {exp.llm_calls} LLM call(s) | "
        f"{exp.completion_tokens} completion tokens"
    )
    if exp.meta.get("stages"):
        foot += f" | stages: {exp.meta['stages']}"
    st.caption(foot)


# ------------------------------------------------------------------- sidebar
client, client_error = get_client()
gold = get_gold()

with st.sidebar:
    st.title("Prescripto Lens")
    #st.caption(f"v{__version__} - prescriptions in plain language")

    if client is not None:
        st.success(f"Azure Deployment : `{client.cfg.deployment}`")
        #st.caption(f"mode: {client.cfg.mode}")
    else:
        st.warning("LLM not configured")
        st.caption(
            "Copy `.env.example` to `.env` and fill in your Azure endpoint, key "
            "and deployment name. The rule-based baseline works without it."
        )
        if client_error and client_error != "not configured":
            st.caption(f"detail: {client_error}")

    st.divider()
    mode = st.radio(
        "What to run",
        ["One system", "Compare all three"],
        
    )
    system = "rule"
    if mode == "One system":
        system = st.selectbox(
            "System", list(SYSTEMS), index=2 if client else 0,
            format_func=lambda s: LABELS[s],
        )
    offline = st.checkbox(
        "Offline retrieval", value=False,
        
    )
    verify = st.checkbox(
        "Self verification (system C)", value=True,
        
    )

    st.divider()
    stats = kb_stats()
    st.caption(
        f"Knowledge base: {stats['drugs']} drugs, {stats['brand_aliases']} brand "
        f"names, {stats['high_risk']} high risk. Gold set: {len(gold)} cases."
    )
   

# ---------------------------------------------------------------------- main
st.header(" Prescription Explanation for Patient Understanding and Safe Medication Use")
st.write(
    "Note:prescription in Indian shorthand "
    "(`T. Dolo 650 1-0-1 x 5 days AF`)  "
)

gold_by_id = {c.case_id: c for c in gold}
choices = ["(type my own)"] + [
    f"{c.case_id} - {c.category}/{c.difficulty} - {c.input_text.splitlines()[0][:52]}"
    for c in gold
]
picked = st.selectbox("Load an example from the gold set", choices)
gold_case = None
default_text = "T. Dolo 650 1-0-1 x 5 days AF\nCap Omez 20mg 1-0-0 BF x 5 days"
if picked != "(type my own)":
    gold_case = gold_by_id[picked.split(" - ")[0]]
    default_text = gold_case.input_text

text = st.text_area("Prescription text", value=default_text, height=170, key=picked)

if gold_case is not None:
    with st.expander(f"Gold reference for {gold_case.case_id} ({gold_case.notes})"):
        st.write(gold_case.reference)
        st.caption(f"{len(gold_case.must_include)} critical-fact checks")

go = st.button("Explain", type="primary", use_container_width=True)

if go:
    if not text.strip():
        st.warning("Please paste some prescription text first.")
        st.stop()

    tab_out, tab_safety, tab_facts = st.tabs(
        ["Explanation", "Safety checks", "What the parser read"]
    )

    with tab_safety:
        flags = safety_check(parse(text))
        if not flags:
            st.success("No automatic safety flags were raised for this prescription.")
        else:
            st.caption(
                f"{len(flags)} flag(s), worst first. These come from deterministic "
                f"rules, not from the language model."
            )
            for f in flags:
                severity_box(f.severity, f.message)

    with tab_facts:
        show_parsed_facts(text)

    with tab_out:
        systems = list(SYSTEMS) if mode == "Compare all three" else [system]
        needed = [s for s in systems if s != "rule"]
        if needed and client is None:
            st.error(
                f"{', '.join(LABELS[s] for s in needed)} need Azure credentials. "
                f"Showing the rule-based baseline only."
            )
            systems = ["rule"]

        if len(systems) == 1:
            with st.spinner(f"Running {LABELS[systems[0]]}..."):
                started = time.perf_counter()
                exp = run_system(
                    systems[0], "ui", text, client, offline=offline, verify=verify,
                )
            st.subheader(LABELS[systems[0]])
            render_result(exp, gold_case)
        else:
            cols = st.columns(len(systems))
            for col, sys_name in zip(cols, systems):
                with col:
                    st.subheader(LABELS[sys_name])
                    with st.spinner("Running..."):
                        try:
                            exp = run_system(
                                sys_name, "ui", text, client,
                                offline=offline, verify=verify,
                            )
                        except Exception as exc:
                            st.error(f"{type(exc).__name__}: {exc}")
                            continue
                    render_result(exp, gold_case)
