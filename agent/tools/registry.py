"""The agent's tools: name, description, argument model and a runner, in one place.

Each tool's JSON schema is generated from the same Pydantic model that validates its
arguments, so the schema the model sees and the checks the code runs can't drift apart.

A tool returns the text the model reads and its *evidence*: the clause IDs the model may cite
afterwards. Search and lookups give the chunks they returned. Calculation tools give the
clauses their code implements (e.g. CD109-4.2 for Equation 4.2), so a cited calculation
passes the citation check. check_limit gives none: its `source_clause` is the model's own
input, and counting it would let the model cite something it never retrieved.

Tool descriptions are a CLAUDE DRAFT (2026-10-25) for Ben to rewrite (PLAN.md Phase 4).
"""

import json
import logging
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from azure.core.exceptions import AzureError
from openai import OpenAIError
from pydantic import BaseModel, ValidationError

from agent.rag import CONTEXT_FIELDS, format_context
from agent.tools import equations
from agent.tools.charts import Chart, CheckChartArgs, check_chart, describe, load_charts
from agent.tools.check_limit import CheckLimitArgs, check_limit
from agent.tools.lookup import (
    AmbiguousCitationError,
    Clause,
    ClauseNotFoundError,
    LookupArgs,
    get_clause,
    get_table,
)
from agent.tools.search_tool import Embed, SearchStandardsArgs, search_standards
from index.search import Hit, SearchAPI

logger = logging.getLogger(__name__)

MAX_ERROR_CHARS = 500  # a validation error can list every field; keep the model's input short


@dataclass(frozen=True)
class ToolOutput:
    text: str  # what the model reads
    evidence: tuple[str, ...] = ()  # IDs the model may now cite


@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    args_model: type[BaseModel]
    run: Callable[..., ToolOutput]

    def schema(self) -> dict[str, Any]:
        """Responses API function-tool definition. Not `strict`: strict mode needs every field
        required, and the Pydantic model validates the arguments anyway."""
        return {
            "type": "function",
            "name": self.name,
            "description": self.description,
            "parameters": self.args_model.model_json_schema(),
            "strict": False,
        }


@dataclass(frozen=True)
class ToolCall:
    """One call as it happened, for the trace and the result."""

    name: str
    arguments: Mapping[str, Any] | str  # the raw string if it wasn't valid JSON
    ok: bool
    output: str
    evidence: tuple[str, ...]
    seconds: float


def _hits_output(hits: Sequence[Hit]) -> ToolOutput:
    if not hits:
        return ToolOutput("No clauses found. Try other wording, or a different standard.")
    return ToolOutput(format_context(hits), tuple(h.chunk_id for h in hits))


def _clause_output(clause: Clause) -> ToolOutput:
    # Shown in the same <clause> form as search results, so the model reads one format.
    hit = Hit(
        chunk_id=clause.citation_id,
        citation_id=clause.citation_id,
        score=0.0,
        fields=clause.model_dump(include=set(CONTEXT_FIELDS)),
    )
    refs = f"\n\nIt refers to: {', '.join(clause.refs)}" if clause.refs else ""
    return ToolOutput(format_context([hit]) + refs, (clause.citation_id,))


def _model_output(result: BaseModel, evidence: Sequence[str] = ()) -> ToolOutput:
    return ToolOutput(result.model_dump_json(), tuple(evidence))


def _equation(fn: Callable[..., equations.EquationResult]) -> Callable[..., ToolOutput]:
    def run(**kwargs: object) -> ToolOutput:
        result = fn(**kwargs)
        return _model_output(result, result.clauses)

    return run


_EQUATIONS: tuple[tuple[str, str, type[BaseModel], Callable[..., Any]], ...] = (
    (
        "superelevation",
        "Crossfall for a horizontal curve under CD 109 cl. 4.1-4.4: adverse camber, 2.5%, or "
        "Equation 4.2 capped at 7% rural / 5% urban. Use it for any superelevation value; "
        "never work one out yourself, and quote its value in your answer. If the question "
        "doesn't say rural or urban, call it for both and give both results.",
        equations.SuperelevationArgs,
        equations.superelevation,
    ),
    (
        "transition_length",
        "Basic transition curve length, CD 109 Equation 4.13 (L = V³/46.7qR), with the "
        "cl. 4.14 limits on q and the cl. 4.15 √(24R) comparison.",
        equations.TransitionArgs,
        equations.transition_length,
    ),
    (
        "bendiness",
        "Bendiness B (degrees/km) from the total change of direction over a length, as "
        "defined in CD 109 (NOTE to cl. 2.2). Input to design speed.",
        equations.BendinessArgs,
        equations.bendiness,
    ),
    (
        "alignment_constraint",
        "Alignment constraint Ac, CD 109 Equation 2.2a (dual) or 2.2b (single), for reading "
        "design speed off Figure 2.1.",
        equations.AlignmentConstraintArgs,
        equations.alignment_constraint,
    ),
    (
        "harmonic_mean_visibility",
        "Harmonic mean visibility VISI from measured sight distances, CD 109 Equation 2.8.1 "
        "(new roads).",
        equations.HarmonicVisibilityArgs,
        equations.harmonic_mean_visibility,
    ),
    (
        "empirical_visibility",
        "Harmonic mean visibility VISI estimated from verge width and bendiness, CD 109 "
        "Equation 2.8.2 (existing roads).",
        equations.EmpiricalVisibilityArgs,
        equations.empirical_visibility,
    ),
)


def _chart_tool(charts: Mapping[str, Chart]) -> Tool:
    def run(**kwargs: object) -> ToolOutput:
        result = check_chart(charts, **kwargs)
        return _model_output(result, result.clauses)

    return Tool("check_chart", describe(charts), CheckChartArgs, run)


def build_tools(
    search_client: SearchAPI, embed: Embed, charts: Mapping[str, Chart] | None = None
) -> tuple[Tool, ...]:
    """Every tool the agent can call, bound to the live clients. Charts default to the files
    in data/charts/; with none there, there's no check_chart tool."""
    charts = load_charts() if charts is None else charts
    return (
        Tool(
            "search_standards",
            "Search the indexed DMRB standards (CD 109 road geometry, CD 123 junctions, "
            "CG 501 drainage, GG 101 how the DMRB works) for clauses, tables and figures "
            "relevant to a query. Returns them with IDs and the citation form to copy. Search "
            "again with other wording, or one standard, if the results don't answer it.",
            SearchStandardsArgs,
            lambda **kw: _hits_output(search_standards(search_client, embed, **kw)),
        ),
        Tool(
            "get_clause",
            "Fetch one clause, table, glossary or figure by citation, e.g. 'CD 109 cl. 4.13' "
            "or 'CD 109 Table 2.10'. Use it to follow a reference in a clause you've read "
            "('in accordance with Table 2.10') rather than searching for it.",
            LookupArgs,
            lambda **kw: _clause_output(get_clause(search_client, **kw)),
        ),
        Tool(
            "get_table",
            "Fetch one table by citation, e.g. 'CD 109 Table 5.1'. Refuses anything that "
            "isn't a table.",
            LookupArgs,
            lambda **kw: _clause_output(get_table(search_client, **kw)),
        ),
        *(Tool(name, desc, model, _equation(fn)) for name, desc, model, fn in _EQUATIONS),
        Tool(
            "check_limit",
            "Compare a design value against a limit from the standard (<=, <, >=, > or "
            "between), converting units. Use it for every pass/fail comparison; never compare "
            "numbers yourself. source_clause is the ID of the clause the limit comes from.",
            CheckLimitArgs,
            lambda **kw: _model_output(check_limit(**kw)),
        ),
        *((_chart_tool(charts),) if charts else ()),
    )


def _error(message: str) -> str:
    return json.dumps({"error": message[:MAX_ERROR_CHARS]})


def _parse_arguments(raw: str) -> dict[str, Any]:
    arguments = json.loads(raw or "{}")
    if not isinstance(arguments, dict):
        raise ValueError("arguments must be a JSON object")
    return arguments


def _invalid(err: ValidationError) -> str:
    first = err.errors()[0]
    field = ".".join(str(p) for p in first["loc"]) or "arguments"
    return f"invalid arguments ({err.error_count()} error(s)); {field}: {first['msg']}"


def _execute(tool: Tool, arguments: Mapping[str, Any]) -> ToolOutput:
    """Validate against the tool's model first: its extra="forbid" rejects unknown keys, so a
    model-supplied "client" can never collide with the client a runner binds."""
    try:
        validated = tool.args_model.model_validate(arguments)
    except ValidationError as err:
        raise _BadCall(_invalid(err)) from None
    return tool.run(**validated.model_dump())


class _BadCall(ValueError):
    """The model's call was wrong; the message says how."""


def run_tool(
    tools: Mapping[str, Tool],
    name: str,
    raw_arguments: str,
    clock: Callable[[], float] = time.monotonic,
) -> ToolCall:
    """Run one call. Anything that goes wrong becomes an error the model can read; only Azure
    and OpenAI failures are raised, for the caller to report."""
    started = clock()
    arguments: Mapping[str, Any] | str = raw_arguments

    def failed(message: str) -> ToolCall:
        return ToolCall(name, arguments, False, _error(message), (), clock() - started)

    try:
        arguments = _parse_arguments(raw_arguments)
        tool = tools.get(name)
        if tool is None:
            return failed(f"no tool named {name!r}; available: {', '.join(tools)}")
        output = _execute(tool, arguments)
    except (AzureError, OpenAIError):
        raise
    except ValidationError as err:
        # Arguments already passed, so this came from inside the tool (e.g. an index record
        # that doesn't fit Clause): our fault, not the model's.
        logger.exception("Tool %s failed validating its own data", name)
        return failed(f"the tool failed internally ({err.error_count()} validation error(s))")
    except (ValueError, ClauseNotFoundError, AmbiguousCitationError, ArithmeticError) as err:
        # ValueError includes bad JSON and _BadCall; ArithmeticError is a Decimal trap the input
        # bounds missed. A bare KeyError is our bug, so it falls through to the catch-all.
        return failed(str(err) or type(err).__name__)
    except Exception as err:  # noqa: BLE001 - one bad tool call must not crash the run
        logger.exception("Tool %s raised unexpectedly", name)
        return failed(f"the tool failed internally ({type(err).__name__})")
    return ToolCall(name, arguments, True, output.text, output.evidence, clock() - started)
