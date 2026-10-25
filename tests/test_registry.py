"""Tool registry: schemas, and run_tool turning every bad call into a readable error."""

import json
from decimal import InvalidOperation

import pytest
from azure.core.exceptions import ServiceRequestError
from pydantic import BaseModel, ConfigDict, ValidationError

from agent.tools.lookup import Clause, ClauseNotFoundError
from agent.tools.registry import Tool, ToolOutput, build_tools, run_tool
from tests.test_search_tool import FakeSearch, _doc, _embed


def _internal_validation_error() -> ValidationError:
    try:
        Clause.model_validate({"citation_id": "x"})
    except ValidationError as err:
        return err
    raise AssertionError("expected a validation error")


@pytest.fixture
def tools() -> dict[str, Tool]:
    client = FakeSearch([_doc("CD109-4.2"), _doc("CD109-4.2#2")])
    return {t.name: t for t in build_tools(client, _embed)}


def test_every_tool_has_a_valid_function_schema(tools):
    assert set(tools) >= {"search_standards", "get_clause", "superelevation", "check_limit"}
    for tool in tools.values():
        schema = tool.schema()
        assert schema["type"] == "function" and schema["name"] == tool.name
        assert schema["parameters"]["type"] == "object"
        assert len(tool.description) > 40, tool.name


def test_search_returns_clause_blocks_and_their_ids_as_evidence(tools):
    call = run_tool(tools, "search_standards", json.dumps({"query": "superelevation"}))
    assert call.ok
    assert '<clause id="CD109-4.2"' in call.output
    assert call.evidence == ("CD109-4.2", "CD109-4.2#2")


def test_equation_evidence_is_the_clauses_its_code_implements(tools):
    args = {"design_speed_kph": 100, "radius_m": 510, "area": "rural"}
    call = run_tool(tools, "superelevation", json.dumps(args))
    assert call.ok
    assert json.loads(call.output)["value"] == "6.93"
    assert "CD109-4.2" in call.evidence


def test_check_limit_gives_no_evidence_because_its_clause_is_the_models_input(tools):
    args = {"value": 5, "unit": "%", "comparison": "<=", "limit": 4, "source_clause": "X-1"}
    call = run_tool(tools, "check_limit", json.dumps(args))
    assert call.ok and "FAIL" in call.output
    assert call.evidence == ()


@pytest.mark.parametrize(
    ("name", "raw", "expected"),
    [
        ("search_standards", "{not json", "Expecting"),
        ("search_standards", "[1, 2]", "JSON object"),
        ("no_such_tool", "{}", "no tool named"),
        ("superelevation", '{"design_speed_kph": 100}', "invalid arguments"),
        ("superelevation", '{"design_speed_kph": 100, "radius_m": 0, "area": "rural"}', "radius"),
    ],
)
def test_bad_calls_become_errors_the_model_can_read(tools, name, raw, expected):
    call = run_tool(tools, name, raw)
    assert not call.ok and call.evidence == ()
    assert expected in json.loads(call.output)["error"]


def test_a_missing_clause_is_an_error_not_a_crash():
    def missing(**kwargs):
        raise ClauseNotFoundError("CD109-99.9 is not in the index")

    tools = {"get_clause": Tool("get_clause", "x" * 50, _Args, missing)}
    call = run_tool(tools, "get_clause", '{"citation": "CD 109 cl. 99.9"}')
    assert not call.ok and "not in the index" in call.output


def test_unparseable_arguments_are_kept_raw_for_the_trace(tools):
    assert run_tool(tools, "search_standards", "{oops").arguments == "{oops"


def test_long_errors_are_truncated():
    def noisy(**kwargs) -> ToolOutput:
        raise ValueError("x" * 5000)

    call = run_tool({"t": Tool("t", "d" * 50, _Args, noisy)}, "t", "{}")
    assert len(json.loads(call.output)["error"]) == 500


class _Args(BaseModel):
    model_config = ConfigDict(extra="forbid")

    citation: str = ""


@pytest.mark.parametrize("reserved", ["client", "embed", "self"])
def test_reserved_argument_names_are_rejected_not_bound(tools, reserved):
    # Before validation-first, {"client": 1} collided with the bound search client: TypeError.
    raw = json.dumps({"query": "gradient", reserved: 1})
    call = run_tool(tools, "search_standards", raw)
    assert not call.ok and "invalid arguments" in call.output


@pytest.mark.parametrize(
    ("exc", "expected"),
    [
        (InvalidOperation(), "InvalidOperation"),
        (KeyError("text"), "failed internally (KeyError)"),
        (_internal_validation_error(), "failed internally"),
    ],
)
def test_any_tool_failure_becomes_an_error_not_a_crash(exc, expected):
    def broken(**kwargs):
        raise exc

    call = run_tool({"t": Tool("t", "d" * 50, _Args, broken)}, "t", "{}")
    assert not call.ok and expected in json.loads(call.output)["error"]


def test_azure_failures_are_raised_for_the_caller_to_report():
    def down(**kwargs):
        raise ServiceRequestError("search unreachable")

    with pytest.raises(ServiceRequestError):
        run_tool({"t": Tool("t", "d" * 50, _Args, down)}, "t", "{}")


def test_get_clause_shows_the_clause_block_and_where_it_points():
    doc = _doc("CD109-4.2") | {
        "doc_code": "CD 109",
        "version": "Revision 1",
        "label": "4.2",
        "requirement_type": "shall",
        "heading_path": "4. Horizontal alignment",
        "page": 21,
        "url": "https://example.org",
        "refs": ["CD109-Table-2.10"],
    }
    tools = {t.name: t for t in build_tools(FakeSearch([doc]), _embed)}
    call = run_tool(tools, "get_clause", '{"citation": "CD 109 cl. 4.2"}')
    assert call.ok and call.evidence == ("CD109-4.2",)
    assert '<clause id="CD109-4.2"' in call.output
    assert call.output.endswith("It refers to: CD109-Table-2.10")


def test_an_empty_search_says_so():
    tools = {t.name: t for t in build_tools(FakeSearch([]), _embed)}
    call = run_tool(tools, "search_standards", '{"query": "tram lines"}')
    assert call.ok and call.evidence == () and "No clauses found" in call.output
