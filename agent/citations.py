"""Citation verification: every clause an answer cites must be one the model was actually shown.

A citation to a clause that was never retrieved is a hallucination, however plausible it looks.
The checks run in code after every answer; failures go back to the model once, then to a human.

The model must use one rigid inline form, e.g. [CD 109 cl. 5.3.1] or [CD 109 cl. 5.3.1, 5.3.2;
CD 123 cl. 3.4]. Anything reference-like that isn't in that form fails the gate rather than being
skipped, because a reference the checker can't read is a reference it can't verify.
"""

import logging
import re
from collections.abc import Iterable

from pydantic import BaseModel, ConfigDict

from agent.answer import Answer

logger = logging.getLogger(__name__)


class CitationError(ValueError):
    """A citation string that doesn't name both a document and a clause."""


# Doc codes are uppercase on purpose (no IGNORECASE) so prose like "No 123" isn't a citation.
_DOC = re.compile(r"^\s*([A-Z]{2})\s?(\d{3})\s*,?\s*")
_CLAUSE = re.compile(r"^\d+(?:\.\d+)*[a-zA-Z]?$")  # 5.3.1, 5.3.1a
_CHUNK_ID = re.compile(r"^([A-Z]{2})\s?(\d{3})\s*-\s*(\d+(?:\.\d+)*[A-Z]?)$")
_CONNECTORS = re.compile(r"\bcl(?:ause)?s?\b\.?|\band\b|[,&]", re.IGNORECASE)
_BRACKETS = re.compile(r"\[([^\[\]]*)\]")
# Double-quoted spans are DMRB wording quoted verbatim, which may say "see Table 5.3".
_QUOTED = re.compile(r"\"[^\"]*\"|“[^”]*”")
# Reference-like text that should have been a bracketed citation.
_LOOSE_REF = re.compile(
    r"\b(?:cl(?:ause)?s?\.?|para(?:graph)?s?\.?|table|figure|fig\.)\s*\d"
    r"|\b(?-i:[A-Z]{2})\s?\d{3}\s*,?\s*-?\s*\d+\.\d",
    re.IGNORECASE,
)


def _clean_clause(clause: str) -> str:
    """'05.3.1A' -> '5.3.1a': leading zeros and letter case shouldn't change identity."""
    *parts, last = clause.split(".")
    letter = last[-1].lower() if last[-1].isalpha() else ""
    digits = last[:-1] if letter else last
    return ".".join(str(int(p)) for p in [*parts, digits]) + letter


def chunk_id(doc_code: str, clause: str) -> str:
    """'CD 109', '5.3.1' -> 'CD109-5.3.1'. The one place the ID format is defined."""
    return f"{doc_code.replace(' ', '').upper()}-{_clean_clause(clause)}"


def _parse_group(group: str) -> tuple[str, ...]:
    """Parse the inside of one [...] group into chunk IDs; raise if any part isn't a citation."""
    ids, doc = [], None
    for segment in group.split(";"):
        match = _DOC.match(segment)
        if match:
            doc, segment = match.group(1) + match.group(2), segment[match.end() :]
        tokens = _CONNECTORS.sub(" ", segment).split()
        if doc is None or not tokens or not all(_CLAUSE.match(t) for t in tokens):
            raise CitationError(f"[{group}] is not a clause citation like [CD 109 cl. 5.3.1]")
        ids.extend(chunk_id(doc, t) for t in tokens)
    return tuple(ids)


def normalise_citation(raw: str) -> str:
    """One citation in either form ('CD109-5.3.1' or 'CD 109 cl. 5.3.1') -> chunk ID."""
    text = raw.strip().strip("[]").strip()
    if match := _CHUNK_ID.match(text.upper()):
        return chunk_id(match.group(1) + match.group(2), match.group(3))
    ids = _parse_group(text)
    if len(ids) != 1:
        raise CitationError(f"{raw!r} should be exactly one clause, like 'CD109-5.3.1'")
    return ids[0]


def _scan_text(text: str) -> tuple[tuple[str, ...], tuple[str, ...], tuple[str, ...]]:
    """Return (cited IDs in order, malformed [...] groups, unbracketed references)."""
    cited, malformed = [], []
    for group in _BRACKETS.findall(text):
        if not any(c.isdigit() for c in group):
            continue  # e.g. [sic], not a reference
        try:
            cited.extend(_parse_group(group))
        except CitationError:
            malformed.append(f"[{group}]")
    prose = _QUOTED.sub(" ", _BRACKETS.sub(" ", text))
    loose = tuple(m.group(0).strip() for m in _LOOSE_REF.finditer(prose))
    return tuple(dict.fromkeys(cited)), tuple(malformed), loose


def inline_citations(text: str) -> tuple[str, ...]:
    """Clause IDs cited in the answer text, in first-seen order, without duplicates."""
    return _scan_text(text)[0]


class CitationCheck(BaseModel):
    model_config = ConfigDict(frozen=True)

    invalid: tuple[str, ...] = ()  # `citations` entries that aren't clause IDs
    malformed: tuple[str, ...] = ()  # [...] groups in the text that can't be parsed
    unbracketed: tuple[str, ...] = ()  # references in prose outside the rigid form
    not_retrieved: tuple[str, ...] = ()  # cited, but never shown to the model
    missing_from_list: tuple[str, ...] = ()  # cited in text, absent from `citations`
    missing_from_text: tuple[str, ...] = ()  # in `citations`, never cited in text
    uncited: bool = False  # claims a requirement but cites nothing
    refusal_cites: bool = False  # says not_covered yet cites clauses

    @property
    def ok(self) -> bool:
        return not self.reasons

    @property
    def reasons(self) -> tuple[str, ...]:
        """Plain sentences, written to be fed back to the model on retry."""
        checks = [
            (self.uncited, "The answer gives requirements but has no citations."),
            (self.refusal_cites, "A not_covered answer must not cite clauses."),
            (self.not_retrieved, "Cited clauses not among the retrieved clauses: {}."),
            (self.invalid, "Citations not in clause ID form (e.g. CD109-5.3.1): {}."),
            (self.malformed, "Unreadable citations; use [CD 109 cl. 5.3.1]: {}."),
            (self.unbracketed, "References must be bracketed like [CD 109 cl. 5.3.1]: {}."),
            (self.missing_from_list, "Cited in the text but missing from citations: {}."),
            (self.missing_from_text, "Listed in citations but never cited in the text: {}."),
        ]
        return tuple(
            msg.format(", ".join(v) if isinstance(v, tuple) else "") for v, msg in checks if v
        )


def _parse_listed(citations: Iterable[str]) -> tuple[tuple[str, ...], tuple[str, ...]]:
    valid, invalid = [], []
    for raw in citations:
        try:
            valid.append(normalise_citation(raw))
        except CitationError:
            invalid.append(raw)
    return tuple(dict.fromkeys(valid)), tuple(invalid)


def _retrieved_set(retrieved_ids: Iterable[str]) -> set[str]:
    # Retrieved IDs come from our own index, so a bad one is our bug: log it, don't crash the gate.
    valid = set()
    for raw in retrieved_ids:
        try:
            valid.add(normalise_citation(raw))
        except CitationError:
            logger.warning("Skipping malformed retrieved chunk ID %r", raw)
    return valid


def verify_citations(answer: Answer, retrieved_ids: Iterable[str]) -> CitationCheck:
    retrieved = _retrieved_set(retrieved_ids)
    listed, invalid = _parse_listed(answer.citations)
    in_text, malformed, unbracketed = _scan_text(answer.answer)
    cited = tuple(dict.fromkeys(listed + in_text))
    refusal = answer.requirement_type == "not_covered"

    return CitationCheck(
        invalid=invalid,
        malformed=malformed,
        unbracketed=unbracketed,
        not_retrieved=tuple(c for c in cited if c not in retrieved),
        missing_from_list=tuple(c for c in in_text if c not in listed),
        missing_from_text=tuple(c for c in listed if c not in in_text),
        uncited=not refusal and not cited,
        refusal_cites=refusal and bool(cited or invalid or malformed),
    )
