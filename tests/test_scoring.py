"""Metric definitions: what each one counts, and over which questions."""

from agent.loop import AgentResult
from agent.rag import FALLBACK_ANSWER
from evals.golden import GoldenQuestion
from evals.scoring import Stat, format_table, score, summarise
from tests.test_rag import _answer


def _result(citations=("CD109-5.1",), requirement="shall", departure=None, escalation=()):
    text = " ".join(f"[{c}]" for c in citations) or "Not covered."
    answer = _answer(text, citations, requirement_type=requirement, is_departure=departure)
    return AgentResult(
        question="q",
        answer=answer,
        check=None,
        evidence=citations,
        tool_calls=(),
        steps=2,
        escalation=escalation,
        input_tokens=1000,
        output_tokens=50,
        seconds=3.0,
    )


GRADIENT = GoldenQuestion(
    id="G01",
    query="5% on a motorway?",
    type="departure",
    expected=["CD 109 cl. 5.1", "CD 109 Table 5.1"],
    requirement="shall",
    departure=True,
)
COMPLIANT = GoldenQuestion(
    id="G03",
    query="Is 3% fine on a motorway?",
    type="departure",
    expected=["CD 109 Table 5.1"],
    departure=False,
)
SPEED = GoldenQuestion(id="G02", query="Speed limit?", type="out_of_scope")


def test_a_perfect_departure_answer():
    s = score(GRADIENT, _result(("CD109-5.1", "CD109-Table-5.1"), departure=True), "agent")
    assert (s.citation_precision, s.citation_recall) == (1.0, 1.0)
    assert s.requirement_ok and s.departure_ok
    assert s.refused_correctly is None and s.refused_wrongly is False


def test_an_extra_citation_lowers_precision_only():
    cited = ("CD109-5.1", "CD109-Table-5.1", "CD109-5.2")
    s = score(GRADIENT, _result(cited, departure=True), "agent")
    assert s.citation_recall == 1.0 and round(s.citation_precision, 3) == 0.667


def test_split_parts_count_as_their_clause_and_bad_ids_count_against():
    s = score(GRADIENT, _result(("CD109-5.1#2", "CD109-Table-5.1#2")), "agent")
    assert s.citation_precision == 1.0 and s.citation_recall == 1.0
    assert score(GRADIENT, _result(("CD109-5.1#dup2",)), "agent").citation_precision == 1.0
    assert score(GRADIENT, _result(("CD109-5.1", "garbage")), "agent").citation_precision == 0.5


def test_citing_nothing_in_scope_is_zero_precision_not_skipped():
    s = score(GRADIENT, _result((), requirement="not_covered"), "agent")
    assert s.citation_precision == 0.0 and s.citation_recall == 0.0
    assert s.refused_wrongly is True and s.requirement_ok is False


def test_null_departure_counts_as_no_departure():
    assert score(COMPLIANT, _result(("CD109-Table-5.1",), departure=None), "agent").departure_ok
    assert not score(GRADIENT, _result(("CD109-5.1",), departure=None), "agent").departure_ok


def test_out_of_scope_scores_on_refusal_only():
    refused = score(SPEED, _result((), requirement="not_covered"), "agent")
    assert refused.refused_correctly is True and refused.refused_wrongly is None
    assert refused.citation_precision is None and refused.citation_recall is None
    assert refused.requirement_ok is None and refused.departure_ok is None
    answered = score(SPEED, _result(("CD109-2.5",)), "agent")
    assert answered.refused_correctly is False and answered.citation_precision is None


def test_a_fallback_after_a_failure_is_not_a_correct_refusal():
    crashed = _result((), requirement="not_covered", escalation=("Stopped at the 90s budget.",))
    crashed = crashed.model_copy(update={"answer": FALLBACK_ANSWER})
    s = score(SPEED, crashed, "agent")
    assert s.refused_correctly is False and s.escalated


def test_summary_reports_each_metrics_denominator():
    scores = [
        score(GRADIENT, _result(("CD109-5.1", "CD109-Table-5.1"), departure=True), "agent"),
        score(SPEED, _result((), requirement="not_covered", escalation=("x",)), "agent"),
    ]
    summary = summarise(scores)
    assert summary["citation precision"] == Stat(1.0, 1)
    assert summary["departure accuracy"] == Stat(1.0, 1, 1)
    assert summary["correct refusals"] == Stat(0.0, 1, 0)  # escalated: not credited
    assert summary["escalated"] == Stat(0.5, 2, 1)
    assert summary["input tokens / q"].value == 1000


def test_table_shows_denominators_na_and_failures():
    rag = [score(GRADIENT, _result(("CD109-5.1",)), "rag")]
    agent = [score(SPEED, _result((), requirement="not_covered"), "agent")]
    table = format_table({"rag": rag, "agent": agent}, {"rag": 0, "agent": 2})
    assert table.splitlines()[0] == "| Metric | rag | agent |"
    assert "| citation recall | 50% (n=1) | n/a |" in table
    assert "| departure accuracy | 0% (0/1) | n/a |" in table
    assert "| correct refusals | n/a | 100% (1/1) |" in table
    assert "| input tokens / q | 1,000 | 1,000 |" in table
    assert "| seconds / q | 3.0 | 3.0 |" in table
    assert "| failed to run | 0 | 2 |" in table
