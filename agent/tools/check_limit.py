"""check_limit: the only place a numeric comparison against a DMRB limit happens.

The model extracts the numbers and the clause; this code decides pass or fail. Inputs are
bounded and all arithmetic runs in the EXACT context, so no verdict ever depends on rounding.
"""

from decimal import Decimal
from typing import Literal, Self

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_serializer,
    field_validator,
    model_validator,
)

from agent.tools.units import EXACT, Unit, check_same_dimension, convert

Comparison = Literal["<=", "<", ">=", ">", "between"]

# Real design values are nowhere near these bounds. They keep every input exactly
# representable in EXACT's precision and turn absurd inputs into a readable error.
MAX_SIGNIFICANT_DIGITS = 15
MAX_ABS_EXPONENT = 9

_FAIL_WORDING = {"<=": "exceeded by", "<": "exceeded by", ">=": "short by", ">": "short by"}


def _fmt(number: Decimal) -> str:
    """Plain notation, no trailing zeros, no '-0': '1000' not '1E+3'."""
    cleaned = EXACT.plus(number.normalize(EXACT))  # plus() turns -0 into 0
    return format(cleaned, "f")


class CheckLimitArgs(BaseModel):
    """Arguments the agent sends. Also the source of the tool's JSON schema."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    value: Decimal = Field(description="The design value being checked")
    unit: Unit = Field(description="Unit of the design value")
    comparison: Comparison = Field(
        description="How value must relate to limit. 'between' means limit <= value <= "
        "upper_limit (inclusive)"
    )
    limit: Decimal = Field(description="The limit from the standard (lower bound for 'between')")
    limit_unit: Unit | None = Field(default=None, description="Unit of limit, if not `unit`")
    upper_limit: Decimal | None = Field(default=None, description="Upper bound, 'between' only")
    source_clause: str = Field(min_length=1, description="Clause ID the limit comes from")

    @field_validator("value", "limit", "upper_limit", mode="before")
    @classmethod
    def floats_via_str(cls, raw: object) -> object:
        # Decimal(0.1) would carry the binary error; Decimal("0.1") does not.
        return str(raw) if isinstance(raw, float) else raw

    @field_validator("value", "limit", "upper_limit")
    @classmethod
    def within_bounds(cls, number: Decimal | None) -> Decimal | None:
        if number is None or number.is_zero():
            return number
        # Count digits from the tuple, not via normalize(), which would itself overflow.
        digits = len("".join(map(str, number.as_tuple().digits)).rstrip("0"))
        if digits > MAX_SIGNIFICANT_DIGITS or abs(number.adjusted()) > MAX_ABS_EXPONENT:
            raise ValueError(
                f"{number} is out of range: use at most {MAX_SIGNIFICANT_DIGITS} significant "
                f"digits and a magnitude between 1e-{MAX_ABS_EXPONENT} and 1e{MAX_ABS_EXPONENT}"
            )
        return number

    @field_validator("unit", "limit_unit", mode="before")
    @classmethod
    def unit_case_insensitive(cls, raw: object) -> object:
        return raw.strip().lower() if isinstance(raw, str) else raw

    @field_validator("source_clause")
    @classmethod
    def clause_not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("source_clause must name the clause the limit comes from")
        return value.strip()

    @model_validator(mode="after")
    def consistent(self) -> Self:
        if self.comparison == "between":
            if self.upper_limit is None:
                raise ValueError("upper_limit is required when comparison is 'between'")
            if self.upper_limit <= self.limit:
                raise ValueError("upper_limit must be greater than limit")
        elif self.upper_limit is not None:
            raise ValueError("upper_limit is only used with 'between'")
        if self.limit_unit is not None:
            check_same_dimension(self.unit, self.limit_unit)  # UnitError -> ValidationError
        return self


class CheckResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    passed: bool
    value: Decimal  # converted into the limit's unit
    unit: Unit
    comparison: Comparison
    limit: Decimal
    upper_limit: Decimal | None
    margin: Decimal  # headroom to the nearest bound; negative means by how much it fails
    source_clause: str
    explanation: str

    @field_serializer("value", "limit", "upper_limit", "margin")
    def plain_numbers(self, number: Decimal | None) -> str | None:
        # Strings, not floats: the result goes back to the model as JSON and must stay exact.
        return None if number is None else _fmt(number)


def _margin(args: CheckLimitArgs, value: Decimal) -> Decimal:
    match args.comparison:
        case "<=" | "<":
            return EXACT.subtract(args.limit, value)
        case ">=" | ">":
            return EXACT.subtract(value, args.limit)
        case "between":
            return min(EXACT.subtract(value, args.limit), EXACT.subtract(args.upper_limit, value))


def _passes(comparison: Comparison, margin: Decimal) -> bool:
    # Strict comparisons fail at zero margin; inclusive ones (and 'between') pass.
    return margin > 0 if comparison in ("<", ">") else margin >= 0


def _explain(args: CheckLimitArgs, value: Decimal, unit: str, margin: Decimal, ok: bool) -> str:
    v, lo, m = _fmt(value), _fmt(args.limit), _fmt(abs(margin))
    if args.comparison == "between":
        rule = f"{lo} {unit} <= {v} {unit} <= {_fmt(args.upper_limit)} {unit}"
        detail = f"margin {m} {unit}" if ok else f"outside range by {m} {unit}"
    else:
        rule = f"{v} {unit} {args.comparison} {lo} {unit}"
        if ok:
            detail = f"margin {m} {unit}"
        elif margin == 0:
            detail = "equals the limit"  # only reachable for strict < and >
        else:
            detail = f"{_FAIL_WORDING[args.comparison]} {m} {unit}"
    return f"{'PASS' if ok else 'FAIL'}: {rule} ({detail}) [{args.source_clause}]"


def check_limit(**kwargs: object) -> CheckResult:
    """Validate raw tool arguments, then compare value against the limit in the limit's unit.

    Raises pydantic.ValidationError for any bad argument, including unknown or mixed units.
    """
    args = CheckLimitArgs.model_validate(kwargs)
    unit = args.limit_unit or args.unit
    value = convert(args.value, args.unit, unit)
    margin = _margin(args, value)
    ok = _passes(args.comparison, margin)
    return CheckResult(
        passed=ok,
        value=value,
        unit=unit,
        comparison=args.comparison,
        limit=args.limit,
        upper_limit=args.upper_limit,
        margin=margin,
        source_clause=args.source_clause,
        explanation=_explain(args, value, unit, margin, ok),
    )
