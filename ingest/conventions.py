"""DMRB drafting conventions from GG 101 (Version 0.1.0), encoded so the parser can rely on them.

GG 101 defines how every DMRB document is written: what each verb means (Introduction,
"Verbal forms"), how clauses are numbered (Appendix B) and how documents are coded
(Appendix A1). Each rule here cites where it comes from, so a reviewer can check it.
"""

import re
from dataclasses import dataclass
from enum import Enum
from typing import Literal

Verb = Literal["must", "shall", "should", "may", "note"]


class ConventionError(ValueError):
    """Text that doesn't follow the GG 101 scheme it was expected to follow."""


# --- Verbal forms (GG 101 Introduction, "Verbal forms"; clauses 2.3-2.15) -----------------------


@dataclass(frozen=True)
class VerbalForm:
    meaning: str
    variation: str  # how a designer may depart from it
    departure_applies: bool
    source: str


VERBAL_FORMS: dict[Verb, VerbalForm] = {
    "must": VerbalForm(
        meaning="Statutory or legislative requirement",
        variation="cannot be varied; departures are not applicable",
        departure_applies=False,
        source="GG 101 Verbal forms; cl. 2.3",
    ),
    "shall": VerbalForm(
        meaning="Requirement of the Overseeing Organisation",
        variation="only by an approved departure, or a relaxation where the document "
        "explicitly permits one",
        departure_applies=True,
        source="GG 101 Verbal forms; cl. 2.4, 2.9-2.11",
    ),
    "should": VerbalForm(
        meaning="Advice expressed as a recommendation",
        variation="without a departure, but needs a recorded justification and a safety risk "
        "assessment",
        departure_applies=False,
        source="GG 101 Verbal forms; cl. 2.12-2.15",
    ),
    "may": VerbalForm(
        meaning="Advice expressed as a permissible approach",
        variation="through internal review, without recourse to the Overseeing Organisation",
        departure_applies=False,
        source="GG 101 Verbal forms",
    ),
    "note": VerbalForm(
        meaning="Clarification or statement of fact ('can' or present tense)",
        variation="not applicable: notes carry no requirement",
        departure_applies=False,
        source="GG 101 Verbal forms; Appendix B",
    ),
}

_STRENGTH: tuple[Verb, ...] = ("must", "shall", "should", "may")
# Lowercase only, on purpose: DMRB clauses open with their subject ("The layout shall..."), so a
# capitalised "May"/"Shall" is a month or a question, not a modal verb.
_VERB = re.compile(r"\b(must|shall|should|may)\b")
# Text that mentions a verb without using it: quoted ('shall', "should the..."), bracketed
# ("(should)"), or "should" opening a condition ("may, should the OO agree, vary").
_NOT_A_MODAL = re.compile(
    r"\"[^\"]*\"|“[^”]*”|['‘](?:must|shall|should|may)['’]|\((?:must|shall|should|may)\)"
    r"|\bshould\s+(?:the|this|that|a|an|it|any)\b"
)


def classify_verbal_form(text: str) -> Verb:
    """The strongest DMRB verb in a clause body (exclude its NOTEs before calling)."""
    found = set(_VERB.findall(_NOT_A_MODAL.sub(" ", text)))
    return next((verb for verb in _STRENGTH if verb in found), "note")


# --- Clause numbering (GG 101 Appendix B, Table B.1) -------------------------------------------


class ClauseKind(Enum):
    SECTION = "section"  # X.     numbered heading
    REQUIREMENT = "requirement"  # X.X    expected verb: must/shall
    ADVICE = "advice"  # X.X.X  expected verb: should/may, belongs to requirement X.X


# National Application Annex clauses carry a country prefix: E/1.1 (England), NI/1.1
# (Northern Ireland), as seen in GG 101 and CD 109. Add others when a document shows them.
NAA_PREFIX = r"(?:E|NI)/"
_CLAUSE = re.compile(r"^(?:(E|NI)/)?(\d+(?:\.\d+){0,2})\.?$")
_KIND_BY_LEVEL = {1: ClauseKind.SECTION, 2: ClauseKind.REQUIREMENT, 3: ClauseKind.ADVICE}


@dataclass(frozen=True)
class ClauseNumber:
    naa: str | None
    parts: tuple[int, ...]

    @property
    def kind(self) -> ClauseKind:
        return _KIND_BY_LEVEL[len(self.parts)]

    @property
    def parent(self) -> str | None:
        """The requirement an advice clause belongs to (GG 101 Appendix B)."""
        if self.kind is not ClauseKind.ADVICE:
            return None
        return str(ClauseNumber(self.naa, self.parts[:2]))

    def __str__(self) -> str:
        number = ".".join(map(str, self.parts))
        return f"{self.naa}/{number}" if self.naa else number


def parse_clause_number(raw: str) -> ClauseNumber:
    match = _CLAUSE.match(raw.strip())
    if not match:
        raise ConventionError(f"{raw!r} is not a GG 101 clause number (X, X.X or X.X.X)")
    return ClauseNumber(match.group(1), tuple(int(p) for p in match.group(2).split(".")))


_EXPECTED_VERBS: dict[ClauseKind, frozenset[Verb]] = {
    ClauseKind.REQUIREMENT: frozenset({"must", "shall"}),
    ClauseKind.ADVICE: frozenset({"should", "may"}),
}


def clause_consistency_warning(number: ClauseNumber, verb: Verb) -> str | None:
    """Flag a clause whose verb contradicts its numbering level.

    A mismatch usually means the parser split or merged clauses wrongly, so it marks a chunk
    for checking by eye rather than rejecting it.
    """
    expected = _EXPECTED_VERBS.get(number.kind)
    if expected is None or verb in expected:
        return None
    return (
        f"Clause {number} is numbered as {number.kind.value} (expects "
        f"{'/'.join(sorted(expected))}) but its strongest verb is '{verb}'"
    )


# --- Notes (GG 101 Appendix B) ----------------------------------------------------------------

# "NOTE", "NOTE:", "NOTE 2", "NOTE 1:". Uppercase only, so the word "Notes" in prose is ignored.
# Capped at two digits so a line like "NOTE 2018 ..." isn't read as note 2018.
_NOTE = re.compile(r"^NOTE(?:\s+(\d{1,2}))?[\s:.]")


def note_number(line: str) -> int | None:
    """0 for an unnumbered NOTE, n for 'NOTE n', None if the line isn't a note.

    Notes sit immediately after the clause they belong to (GG 101 Appendix B), so the chunker
    attaches them to the preceding clause.
    """
    match = _NOTE.match(line.strip() + " ")
    if not match:
        return None
    return int(match.group(1)) if match.group(1) else 0


# --- Document codes (GG 101 Appendix A1) ------------------------------------------------------

_DISCIPLINES = {
    "G": "General principles & scheme governance",
    "L": "Sustainability & environment",
    "C": "Civil engineering",
    "T": "Technology",
}
_LIFECYCLES = {
    "G": "General information",
    "A": "Appraisal",
    "D": "Design",
    "C": "Construction",
    "M": "Maintenance and operation",
    "S": "Inspection and assessment",
    "Z": "Disposal",
}
_DISCIPLINE_LETTERS = f"[{''.join(_DISCIPLINES)}]"
_LIFECYCLE_LETTERS = f"[{''.join(_LIFECYCLES)}]"
# The two-letter series of a doc code (e.g. "CD"), for reuse in other patterns such as citations.
DOC_SERIES = _DISCIPLINE_LETTERS + _LIFECYCLE_LETTERS
_DOC_CODE = re.compile(rf"^({_DISCIPLINE_LETTERS})({_LIFECYCLE_LETTERS}) ?(\d{{3}})$")


@dataclass(frozen=True)
class DocCode:
    discipline_letter: str
    lifecycle_letter: str
    number: int

    @property
    def discipline(self) -> str:
        return _DISCIPLINES[self.discipline_letter]

    @property
    def lifecycle(self) -> str:
        return _LIFECYCLES[self.lifecycle_letter]

    def __str__(self) -> str:
        return f"{self.discipline_letter}{self.lifecycle_letter} {self.number:03d}"


def parse_doc_code(raw: str) -> DocCode:
    match = _DOC_CODE.match(raw.strip())
    if not match:
        raise ConventionError(
            f"{raw!r} is not a DMRB document code: discipline letter (G/L/C/T), life-cycle "
            "letter (G/A/D/C/M/S/Z), then three digits, e.g. 'CD 109'"
        )
    return DocCode(match.group(1), match.group(2), int(match.group(3)))


# --- Page footer (printed on every page of a downloaded PDF) -----------------------------------

_FOOTER = re.compile(
    r"Downloaded from https?://www\.standardsforhighways\.co\.uk on [^,]+,\s*"
    r"([A-Z]{2}\s*\d{3})\s+((?:Version|Revision)\s+[\d.]+)\s*,\s*published:\s*"
    r"(\d{1,2}-[A-Za-z]{3}-\d{4}|[A-Za-z]{3}[- ]\d{4})"
)


@dataclass(frozen=True)
class Footer:
    doc_code: DocCode
    version: str  # as printed: "Revision 1" or "Version 0.1.0"
    published: str  # as printed: "Mar-2020" or "29-Sep-2021"


def parse_footer(text: str) -> Footer | None:
    """The document's own statement of its version, used to check sources.yaml is right."""
    match = _FOOTER.search(text)
    if not match:
        return None
    code = re.sub(r"\s+", " ", match.group(1))
    return Footer(parse_doc_code(code), match.group(2), match.group(3))
