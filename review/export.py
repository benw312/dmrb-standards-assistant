"""Turn engineer verdicts into golden-set candidates: every review becomes a test case.

    uv run python -m review.export            # prints YAML blocks in evals/golden.yaml's layout

The output is for Ben to read, edit and paste into evals/golden.yaml. Nothing here writes
that file: ground truth stays hand-checked. What a verdict can and can't supply:

- approved: the answer was right, so its citations, requirement strength and departure flag
  are the expected values (the engineer's own `expected` wins if given).
- corrected: the answer was wrong, so only the engineer's note and `expected` are used. The
  requirement strength and departure flag are left as TODOs rather than copied from an answer
  known to be wrong.

`type` can't be inferred reliably (lookup vs multi_hop vs calculation), so it's a best guess
marked TODO.
"""

import json
import sys
from collections.abc import Iterable, Sequence

from agent.citations import CitationError, display_citation
from review.queue import ReviewError, ReviewItem, ReviewQueue

HEADER = """\
# Golden-set candidates from the review queue (review/export.py).
# Read each one, fix the TODOs, then paste it into evals/golden.yaml under your questions.
"""


def _quoted(text: str) -> str:
    # A JSON string is a valid YAML double-quoted scalar; newlines become \\n on one line.
    return json.dumps(text, ensure_ascii=False)


def _cite(chunk_id: str) -> str:
    try:
        return display_citation(chunk_id).strip("[]")
    except CitationError:
        return chunk_id  # shown as stored; golden.yaml's loader will name the problem


def candidate(item: ReviewItem) -> str:
    """One golden.yaml block for a resolved review."""
    if item.verdict is None:
        raise ValueError(f"review {item.id} has no verdict yet")
    flagged, verdict = item.flagged, item.verdict
    approved = verdict.outcome == "approved"
    expected = verdict.expected or (flagged.citations if approved else ())
    # One line, whatever the text holds: a line break here would end the comment and let
    # the rest be read as YAML.
    why = " ".join("; ".join(flagged.reasons).split())
    who = " ".join(verdict.reviewer.split())
    lines = [
        f"# Review {item.id}: {verdict.outcome} by {who}, {item.resolved_at:%Y-%m-%d}. "
        f"Flagged because: {why}",
        f"- id: R{item.id:03d}",
        f"  query: {_quoted(flagged.question)}",
    ]
    if approved and flagged.requirement_type == "not_covered" and not expected:
        lines.append("  type: out_of_scope")
    else:
        guess = "departure" if approved and flagged.is_departure is not None else "lookup"
        lines.append(
            f"  type: {guess}  # TODO: lookup | multi_hop | departure | calculation | out_of_scope"
        )
        if expected:
            lines.append(f"  expected: [{', '.join(_cite(c) for c in expected)}]")
        else:
            lines.append("  expected: []  # TODO: the clauses a correct answer cites")
        if approved:
            if flagged.requirement_type != "not_covered":
                lines.append(f"  requirement: {flagged.requirement_type}")
            if flagged.is_departure is not None:
                lines.append(f"  departure: {str(flagged.is_departure).lower()}")
        else:
            lines.append("  # TODO: requirement: must | shall | should | may | note")
            lines.append("  # TODO: departure: true | false, if the question describes a design")
    answer = verdict.note or flagged.answer
    lines.append(f"  answer: {_quoted(answer)}")
    if approved and verdict.note:
        lines.append(f"  notes: {_quoted('Model answer approved: ' + flagged.answer)}")
    if not approved:
        lines.append(f"  notes: {_quoted('Model answered (wrong): ' + flagged.answer)}")
    return "\n".join(lines) + "\n"


def candidates(items: Iterable[ReviewItem]) -> str:
    blocks = [candidate(item) for item in items if item.verdict is not None]
    return HEADER + "\n" + "\n".join(blocks) if blocks else ""


def main(argv: Sequence[str] | None = None, queue: ReviewQueue | None = None) -> int:
    del argv  # no options yet
    try:
        resolved = (queue or ReviewQueue()).list("resolved", limit=-1)
    except ReviewError as err:
        print(f"error: {err}", file=sys.stderr)
        return 1
    if not resolved:
        print("No verdicts yet: review flagged answers in the demo's Review queue tab first.")
        return 0
    print(candidates(sorted(resolved, key=lambda item: item.id)), end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
