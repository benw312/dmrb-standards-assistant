"""check_chart: read a value off a digitised DMRB chart and say which side of the line it is.

Chart data lives in data/charts/<id>.yaml (written by ingest/digitise.py, checked by an
engineer). Adding a chart is a data change: one generic tool interpolates any of them.

- Linear interpolation between the plotted markers (the charts join them with straight lines).
- Inputs outside the chart are refused: the chart says nothing there.
- Within `tolerance` of the line the verdict is "marginal": reading the chart more precisely
  than that is guesswork, so an engineer decides.
- Data still marked `draft` (machine-read, not yet compared with the figure by an engineer)
  always carries a warning, so the answer is flagged for review.

Decimal throughout, as in check_limit, so a verdict never depends on float rounding.
"""

from collections.abc import Mapping
from decimal import Decimal
from pathlib import Path
from typing import Literal, Self

import yaml
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    computed_field,
    field_serializer,
    field_validator,
    model_validator,
)

from agent.citations import CitationError, normalise_citation
from agent.tools.units import format_decimal

CHART_DIR = Path(__file__).resolve().parents[2] / "data" / "charts"
NOT_CHART_FILES = {"specs.yaml"}  # the digitiser's input, not chart data
DRAFT_WARNING = (
    "chart data was machine-read and has not yet been checked against the figure by an engineer"
)


class ChartError(ValueError):
    """A chart file is malformed, or a call asks for something the chart doesn't cover."""


def _decimal(raw: object) -> object:
    # Decimal(0.1) would carry the binary error; Decimal("0.1") does not.
    return str(raw) if isinstance(raw, float | int) and not isinstance(raw, bool) else raw


class AxisRange(BaseModel):
    model_config = ConfigDict(frozen=True)

    name: str
    unit: str
    min: Decimal
    max: Decimal

    _num = field_validator("min", "max", mode="before")(classmethod(lambda cls, v: _decimal(v)))


class Chart(BaseModel):
    """One data/charts/<id>.yaml file."""

    model_config = ConfigDict(frozen=True)

    id: str
    status: Literal["draft", "checked"]
    source: str
    page: int
    clauses: tuple[str, ...] = Field(min_length=1)
    x: AxisRange
    y: AxisRange
    fails: Literal["below", "above"]
    tolerance: Decimal = Field(gt=0)
    curves: dict[str, tuple[tuple[Decimal, Decimal], ...]] = Field(min_length=1)

    @field_validator("tolerance", mode="before")
    @classmethod
    def _tolerance(cls, raw: object) -> object:
        return _decimal(raw)

    @field_validator("curves", mode="before")
    @classmethod
    def _points(cls, raw: object) -> object:
        if not isinstance(raw, dict):
            return raw
        return {name: [[_decimal(v) for v in p] for p in pts] for name, pts in raw.items()}

    @model_validator(mode="after")
    def _ordered(self) -> Self:
        for name, points in self.curves.items():
            xs = [x for x, _ in points]
            if len(xs) < 2 or xs != sorted(set(xs)):
                raise ValueError(f"curve {name!r}: needs 2+ points with increasing x")
        return self

    @property
    def evidence(self) -> tuple[str, ...]:
        try:
            return tuple(normalise_citation(c) for c in self.clauses)
        except CitationError as err:
            raise ChartError(f"{self.id}: bad clause citation: {err}") from err


def load_charts(directory: Path = CHART_DIR) -> dict[str, Chart]:
    charts = {}
    for path in sorted(directory.glob("*.yaml")):
        if path.name in NOT_CHART_FILES:
            continue
        try:
            chart = Chart.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))
        except (OSError, yaml.YAMLError, ValidationError) as err:
            raise ChartError(f"{path.name}: {err}") from err
        if path.stem != chart.id:
            raise ChartError(f"{path.name}: id {chart.id!r} doesn't match the file name")
        chart.evidence  # noqa: B018 - fail at load, not mid-answer, on a bad citation
        charts[chart.id] = chart
    return charts


class CheckChartArgs(BaseModel):
    """Arguments the agent sends. Also the source of the tool's JSON schema."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    chart_id: str = Field(description="Chart ID, e.g. 'CG501-Figure-7.5.4'")
    x: Decimal = Field(description="Value on the chart's x axis, in its unit")
    y: Decimal = Field(description="Value on the chart's y axis, in its unit")
    curve: str | None = Field(
        default=None, description="Which line, for charts with more than one (e.g. '5 lanes')"
    )

    _num = field_validator("x", "y", mode="before")(classmethod(lambda cls, v: _decimal(v)))


class ChartResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    chart_id: str
    curve: str
    x: Decimal
    y: Decimal
    line_value: Decimal  # the curve's y at this x
    margin: Decimal  # y - line_value
    verdict: Literal["avoid", "acceptable", "marginal"]
    explanation: str
    clauses: tuple[str, ...]
    warnings: tuple[str, ...] = ()

    @computed_field
    @property
    def needs_review(self) -> bool:
        return self.verdict == "marginal" or bool(self.warnings)

    @field_serializer("x", "y", "line_value", "margin")
    def _plain(self, number: Decimal) -> str:
        return format_decimal(number)


def interpolate(points: tuple[tuple[Decimal, Decimal], ...], x: Decimal) -> Decimal:
    """Straight-line interpolation; x must be within the points' range."""
    for (x0, y0), (x1, y1) in zip(points, points[1:], strict=False):
        if x0 <= x <= x1:
            return y0 + (y1 - y0) * (x - x0) / (x1 - x0)
    raise ChartError(f"x = {x} is outside the plotted range {points[0][0]} to {points[-1][0]}")


def _pick_curve(chart: Chart, name: str | None) -> str:
    if name is None:
        if len(chart.curves) == 1:
            return next(iter(chart.curves))
        raise ChartError(f"{chart.id} has several lines; choose curve from {list(chart.curves)}")
    if name not in chart.curves:
        raise ChartError(f"{chart.id} has no line {name!r}; choose from {list(chart.curves)}")
    return name


def check_chart(charts: Mapping[str, Chart], **kwargs: object) -> ChartResult:
    args = CheckChartArgs.model_validate(kwargs)
    chart = charts.get(args.chart_id)
    if chart is None:
        raise ChartError(f"no chart {args.chart_id!r}; available: {', '.join(charts)}")
    curve = _pick_curve(chart, args.curve)
    points = chart.curves[curve]
    if not points[0][0] <= args.x <= points[-1][0]:
        raise ChartError(
            f"{chart.x.name} {args.x} {chart.x.unit} is outside {chart.id} "
            f"({points[0][0]} to {points[-1][0]} {chart.x.unit}): the chart doesn't cover it"
        )
    line = interpolate(points, args.x)
    margin = args.y - line
    on_fail_side = margin < 0 if chart.fails == "below" else margin > 0
    if abs(margin) <= chart.tolerance:
        verdict = "marginal"
        why = f"within {chart.tolerance} {chart.y.unit} of the line: too close to read; review"
    elif on_fail_side:
        verdict = "avoid"
        why = f"{chart.fails} the line, the side {', '.join(chart.clauses)} says to avoid"
    else:
        verdict = "acceptable"
        why = f"not {chart.fails} the line"
    return ChartResult(
        chart_id=chart.id,
        curve=curve,
        x=args.x,
        y=args.y,
        line_value=line.quantize(Decimal("0.001")),
        margin=margin.quantize(Decimal("0.001")),
        verdict=verdict,
        explanation=(
            f"{chart.y.name} {args.y} {chart.y.unit} at {chart.x.name} {args.x} "
            f"{chart.x.unit}: the '{curve}' line is at "
            f"{format_decimal(line.quantize(Decimal('0.01')))} {chart.y.unit}, so the point is "
            f"{why}."
        ),
        clauses=chart.evidence,
        warnings=(DRAFT_WARNING,) if chart.status == "draft" else (),
    )


def describe(charts: Mapping[str, Chart]) -> str:
    """The tool description: what each chart answers, so the model knows when to call it."""
    lines = [
        "Read a DMRB chart: is a point on the side of the line the standard says to avoid? "
        "Use it whenever a clause points to one of these figures; never read a chart "
        "yourself. Inputs outside the chart are refused. Charts:"
    ]
    for c in charts.values():
        curves = f"; curve one of {list(c.curves)}" if len(c.curves) > 1 else ""
        lines.append(
            f"- {c.id} ({', '.join(c.clauses)}): x = {c.x.name} ({c.x.unit}, "
            f"{format_decimal(c.x.min)} to {format_decimal(c.x.max)}), y = {c.y.name} "
            f"({c.y.unit}); avoid {c.fails} the line{curves}"
        )
    return "\n".join(lines)
