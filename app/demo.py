"""Streamlit demo: ask a DMRB question, see the cited answer, its checks and what the agent did.

    uv run streamlit run app/demo.py
    DEMO_REPLAY=1 uv run streamlit run app/demo.py    # recorded answers, no Azure needed

Uses the same engine and response shape as the REST API (agent/engine.py, app/api.py), so
what the demo shows is what the service returns. The last answer is kept in session state,
so changing a setting doesn't wipe it; it's always shown with the question and pipeline
that produced it. An answer that needs an engineer goes on the review queue, which the
second tab works through (app/review_tab.py).
"""

import logging
import sys
import uuid
from pathlib import Path

# `streamlit run` puts app/ on the import path, not the repo root, so agent/ and core/
# wouldn't import. The project isn't an installed package, so add the root here.
ROOT = str(Path(__file__).resolve().parent.parent)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import streamlit as st
from azure.core.exceptions import AzureError
from openai import OpenAIError

from agent import engine as engine_module
from agent.engine import Mode
from agent.rag import RagError
from agent.replay import NotRecordedError, ReplayAnswerer, ReplayError
from agent.trace import TRACE_DIR
from app import review_tab
from app.api import AskResponse, flag_for_review, to_response
from app.presentation import EXAMPLES, STRENGTH_LABELS, cost_line, safe_markdown, status
from app.review_api import get_queue
from core.settings import SettingsError

logger = logging.getLogger(__name__)

st.set_page_config(page_title="DMRB Standards Assistant", page_icon="🛣️", layout="centered")

MODES: dict[str, Mode] = {
    "Agent (tool-calling loop)": "agent",
    "Agent on LangGraph": "graph",
    "RAG baseline (one search, one call)": "rag",
}
BANNERS = {"success": st.success, "info": st.info, "warning": st.warning, "error": st.error}


@st.cache_resource(show_spinner="Connecting to Azure...")
def engine():
    # Looked up on the module at call time, so tests can swap in a fake engine.
    return engine_module.build_answerer()


def _use_example() -> None:
    if st.session_state.example:
        st.session_state.question = st.session_state.example


def _ask(question: str, mode: Mode) -> AskResponse | None:
    try:
        result = engine()(question, mode)
    except NotRecordedError as err:
        st.info(f"{err} Pick one from the list above.")
    except (SettingsError, RagError, ReplayError) as err:
        st.error(f"The assistant isn't configured: {err}")  # actionable, holds no secrets
    except (AzureError, OpenAIError) as err:
        logger.exception("Demo: Azure call failed")
        st.error(f"An Azure service call failed ({type(err).__name__}). Try again.")
    except Exception as err:  # noqa: BLE001 - logged and shown, not swallowed
        logger.exception("Demo question failed")
        st.error(f"Something went wrong ({type(err).__name__}). Check the terminal log.")
    else:
        return flag_for_review(to_response(result, mode, uuid.uuid4().hex[:12]), get_queue())
    return None


def _replay_notice() -> None:
    try:
        recorded = engine()
    except ReplayError as err:
        st.error(str(err))
        return
    if isinstance(recorded, ReplayAnswerer):
        st.info(
            f"**Recorded demo.** No Azure connection: the example questions replay real "
            f"answers recorded on {recorded.recorded_on:%d %b %Y} from {recorded.model}. "
            "Other questions need the live setup (README)."
        )


def _show(response: AskResponse) -> None:
    st.caption(f"**Q:** {safe_markdown(response.question)}  ·  pipeline: {response.mode}")
    banner = status(response)
    BANNERS[banner.kind](banner.headline)
    for reason in banner.reasons:
        st.caption(f"• {safe_markdown(reason)}")
    if response.review_id is not None:
        st.caption(f"On the review queue as #{response.review_id} (Review queue tab).")

    if not banner.show_details:
        with st.expander("Unverified model output (not checked; do not rely on it)"):
            st.markdown(safe_markdown(response.answer))
    else:
        st.markdown(safe_markdown(response.answer))
        cols = st.columns(2)
        cols[0].metric("Governing clause", response.requirement_type.replace("_", " "))
        departure = {True: "Yes", False: "No", None: "n/a"}[response.is_departure]
        cols[1].metric("Departure needed?", departure)
        st.caption(STRENGTH_LABELS[response.requirement_type])
        if response.citations:
            st.subheader("Cited")
            st.markdown(" ".join(f"`{c.cite.strip('[]')}`" for c in response.citations))

    with st.expander("What the agent did"):
        for i, tool in enumerate(response.tools_used, start=1):
            st.markdown(f"{i}. `{tool}`")
        st.caption(cost_line(response))
        if response.trace_id:
            st.caption(f"Full trace: {TRACE_DIR.name}/{response.trace_id}.jsonl")


with st.sidebar:
    st.header("Settings")
    mode = MODES[st.radio("Pipeline", list(MODES))]
    st.caption(
        "Indexed: CD 109 (link design), CD 123 (junctions), CG 501 (drainage), GG 101. "
        "Citations are checked in code; numbers come from calculation tools, never the model."
    )
    if engine_module.replay_enabled():
        _replay_notice()
    st.divider()
    st.caption("Decision support only; confirm against the current standard.")

st.title("DMRB Standards Assistant")
st.write("Ask a highway design question. Every claim cites the clause it comes from.")

ask_tab, queue_tab = st.tabs(["Ask", "Review queue"])

with ask_tab:
    st.selectbox("Try an example", ("", *EXAMPLES), key="example", on_change=_use_example)
    question = st.text_area("Question", key="question", max_chars=1000, height=90)

    # Not disabled while empty: a button that only enables after the text box loses focus
    # swallows the first click.
    if st.button("Ask", type="primary"):
        if not question.strip():
            st.warning("Type a question first.")
        else:
            with st.spinner("Searching the standards..."):
                st.session_state.response = _ask(question.strip(), mode)

    if st.session_state.get("response"):
        _show(st.session_state.response)

with queue_tab:
    review_tab.render(get_queue())
