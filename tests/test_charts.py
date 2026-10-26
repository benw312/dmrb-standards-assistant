"""check_chart: interpolation, verdicts, refusals, draft flags, and the committed chart files."""

import json
from decimal import Decimal

import pytest
import yaml

from agent.tools.charts import (
    DRAFT_WARNING,
    ChartError,
    check_chart,
    describe,
    interpolate,
    load_charts,
)
from agent.tools.registry import build_tools, run_tool
from tests.test_search_tool import FakeSearch, _embed

CHART = {
    "id": "CG501-Figure-7.5.4",
    "status": "checked",
    "source": "CG 501",
    "page": 37,
    "clauses": ["CG 501 cl. 7.5.4"],
    "x": {"name": "flow path length", "unit": "m", "min": 0, "max": 35},
    "y": {"name": "flow path gradient", "unit": "%", "min": 0, "max": 3},
    "fails": "below",
    "tolerance": 0.03,
    "curves": {"minimum desirable": [[0, 0.15], [5, 0.37], [10, 0.62]]},
}


def _charts(tmp_path, **overrides):
    chart = {**CHART, **overrides}
    (tmp_path / f"{chart['id']}.yaml").write_text(yaml.safe_dump(chart), encoding="utf-8")
    (tmp_path / "specs.yaml").write_text("[]", encoding="utf-8")  # must be skipped
    return load_charts(tmp_path)


def _check(charts, **kwargs):
    return check_chart(charts, chart_id="CG501-Figure-7.5.4", **kwargs)


def test_interpolates_between_markers_exactly():
    points = ((Decimal(0), Decimal("0.15")), (Decimal(5), Decimal("0.37")))
    assert interpolate(points, Decimal("2.5")) == Decimal("0.26")


def test_below_the_line_is_avoid_and_cites_the_clause(tmp_path):
    r = _check(_charts(tmp_path), x=7.5, y=0.3)
    assert r.verdict == "avoid" and r.line_value == Decimal("0.495")
    assert r.clauses == ("CG501-7.5.4",) and not r.needs_review


def test_above_the_line_is_acceptable(tmp_path):
    assert _check(_charts(tmp_path), x=7.5, y=0.8).verdict == "acceptable"


def test_within_tolerance_either_side_is_marginal_and_reviewed(tmp_path):
    charts = _charts(tmp_path)
    for y in (0.47, 0.495, 0.52):
        r = _check(charts, x=7.5, y=y)
        assert r.verdict == "marginal" and r.needs_review, y


def test_an_above_chart_flips_the_side(tmp_path):
    assert _check(_charts(tmp_path, fails="above"), x=7.5, y=0.8).verdict == "avoid"


def test_outside_the_chart_is_refused_not_extrapolated(tmp_path):
    with pytest.raises(ChartError, match="doesn't cover it"):
        _check(_charts(tmp_path), x=12, y=1)


def test_draft_data_always_needs_review(tmp_path):
    r = _check(_charts(tmp_path, status="draft"), x=7.5, y=0.8)
    assert r.verdict == "acceptable" and r.warnings == (DRAFT_WARNING,) and r.needs_review


def test_several_lines_need_a_curve_name(tmp_path):
    curves = {"4 lanes": [[0, 0.9], [4, 1.1]], "5 lanes": [[0, 1.1], [4, 1.4]]}
    charts = _charts(tmp_path, curves=curves)
    with pytest.raises(ChartError, match="choose curve"):
        _check(charts, x=1, y=1)
    with pytest.raises(ChartError, match="no line '7 lanes'"):
        _check(charts, x=1, y=1, curve="7 lanes")
    assert _check(charts, x=2, y=1.3, curve="5 lanes").line_value == Decimal("1.25")


def test_unknown_chart_lists_the_ones_there_are(tmp_path):
    with pytest.raises(ChartError, match="available: CG501-Figure-7.5.4"):
        check_chart(_charts(tmp_path), chart_id="CD109-Figure-2.1", x=1, y=1)


@pytest.mark.parametrize(
    ("override", "message"),
    [
        ({"curves": {"line": [[0, 1], [0, 2]]}}, "increasing x"),
        ({"curves": {"line": [[0, 1]]}}, "2\\+ points"),
        ({"clauses": ["not a clause"]}, "bad clause citation"),
        ({"id": "CG501-Figure-9.9"}, "doesn't match the file name"),
    ],
)
def test_bad_chart_files_fail_at_load(tmp_path, override, message):
    chart = {**CHART, **override}
    (tmp_path / "CG501-Figure-7.5.4.yaml").write_text(yaml.safe_dump(chart), encoding="utf-8")
    with pytest.raises(ChartError, match=message):
        load_charts(tmp_path)


def test_the_description_tells_the_model_each_charts_axes(tmp_path):
    text = describe(_charts(tmp_path))
    assert (
        "CG501-Figure-7.5.4 (CG 501 cl. 7.5.4)" in text and "flow path length (m, 0 to 35)" in text
    )


def test_the_agent_gets_check_chart_with_the_clause_as_evidence(tmp_path):
    tools = {t.name: t for t in build_tools(FakeSearch([]), _embed, _charts(tmp_path))}
    call = run_tool(tools, "check_chart", json.dumps({"chart_id": CHART["id"], "x": 7.5, "y": 0.3}))
    assert call.ok and json.loads(call.output)["verdict"] == "avoid"
    assert call.evidence == ("CG501-7.5.4",)
    bad = run_tool(tools, "check_chart", json.dumps({"chart_id": CHART["id"], "x": 99, "y": 1}))
    assert not bad.ok and "doesn't cover it" in bad.output


def test_no_charts_means_no_chart_tool():
    assert "check_chart" not in {t.name for t in build_tools(FakeSearch([]), _embed, {})}


# --- the committed files ----------------------------------------------------------------


def test_committed_charts_load_and_every_marker_reads_back_as_itself():
    charts = load_charts()
    assert {"CG501-Figure-7.5.4", "CG501-Figure-7.5.1"} <= set(charts)
    for chart in charts.values():
        for name, points in chart.curves.items():
            for x, y in points:
                r = check_chart(charts, chart_id=chart.id, x=x, y=y, curve=name)
                assert r.line_value == y.quantize(Decimal("0.001")) and r.verdict == "marginal"


def test_committed_curves_rise_like_the_figures():
    # Both CG 501 charts slope upwards, and the lane lines never cross: a misread marker or
    # a swapped series would break one of these.
    charts = load_charts()
    for chart in charts.values():
        for points in chart.curves.values():
            ys = [y for _, y in points]
            assert ys == sorted(ys), chart.id
    lanes = charts["CG501-Figure-7.5.1"].curves
    for i in range(5):
        assert lanes["4 lanes"][i][1] < lanes["5 lanes"][i][1] < lanes["6 lanes"][i][1]
