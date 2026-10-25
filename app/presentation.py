"""What the demo shows for an answer, as plain data, so it can be tested without a browser.

The wording matters: this is an engineering tool, so the banner never claims more than the
code checked. The citation gate confirms every cited clause was in the text retrieved; it
does not confirm the answer is right.
"""

import re
from dataclasses import dataclass
from typing import Literal

from app.api import AskResponse

STRENGTH_LABELS = {
    "must": "MUST: statutory, no departure possible",
    "shall": "SHALL: requirement; not meeting it needs a departure",
    "should": "SHOULD: advice; not following it needs a recorded justification",
    "may": "MAY: permitted option",
    "note": "NOTE: explanation, not a requirement",
    "not_covered": "No applicable clause found in the indexed standards",
}

EXAMPLES = (
    "Can I use a 5% gradient on a motorway?",
    "What superelevation do I need for a 510 m radius curve at 100 kph on a rural road?",
    "If a route has an AADT of 30,000, is it suitable for a WS2+1 road?",
    "Would a 300 mm wide bollard be allowed in the stopping sight visibility envelope?",
    "What's the speed limit on a motorway?",
)

Kind = Literal["success", "info", "warning", "error"]


@dataclass(frozen=True)
class Status:
    kind: Kind
    headline: str
    reasons: tuple[str, ...] = ()
    show_details: bool = True  # False: the answer failed a check, so its flags aren't shown


def status(response: AskResponse) -> Status:
    """The banner above the answer."""
    if response.escalation:
        return Status(
            "error",
            "Unverified: this answer failed an automatic check. An engineer must review it.",
            tuple(response.escalation),
            show_details=False,
        )
    if response.requirement_type == "not_covered" and not response.citations:
        return Status(
            "info",
            "No applicable clause found in the indexed standards. That doesn't confirm no "
            "requirement exists: only four standards are indexed.",
        )
    checked = "All cited clauses were found in the text retrieved"
    if response.needs_human_review:
        why = ["possible Departure from Standard"] if response.is_departure else []
        if response.confidence == "low":
            why.append("low confidence")
        return Status(
            "warning",
            f"{checked}, but the answer is flagged for engineer review.",
            tuple(why) or ("the model asked for review",),
        )
    return Status("success", f"{checked}. Confirm against the standard before relying on it.")


_IMAGE = re.compile(r"!\[[^\]]*\]\([^)]*\)")


def safe_markdown(text: str) -> str:
    """Model text made safe to render: no images (the browser would fetch any URL the model
    wrote) and no LaTeX ($ is literal, as in costs)."""
    return _IMAGE.sub("[image removed]", text).replace("$", r"\$")


def cost_line(response: AskResponse) -> str:
    tools = len(response.tools_used)
    return (
        f"{response.mode} · {tools} tool call{'s' if tools != 1 else ''} · "
        f"{response.input_tokens:,} in / {response.output_tokens:,} out tokens · "
        f"{response.seconds:.1f}s"
    )
