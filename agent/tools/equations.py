"""CD 109 Revision 1 equations as agent tools: the model supplies inputs, code does the maths.

Square roots, logs and divisions are inexact, so values are computed to 28 significant digits
and rounded only for display. Verdicts against a requirement (the 7%/5% cap, L < √(24R), q
against 0.6 and 0.3) are exact comparisons of products or inputs, so rounding can never flip
them. Advisory range checks (Ac on Figure 2.1, VISI up to 720 m) use the displayed value, so a
warning never contradicts the number shown beside it.

Inputs are bounded to realistic ranges and at most 10 significant digits, which keeps every
exact product well inside the 60-digit context: an extreme input is a validation error, never
an arithmetic one.

Every result lists the clause IDs it relied on, so the agent can cite them, and `warnings`
for anything an engineer should look at; a warning means the answer needs human review.
"""

from decimal import ROUND_HALF_UP, Context, Decimal
from typing import Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    computed_field,
    field_serializer,
    field_validator,
)

from agent.tools.design_speed_table import (
    DESIGN_SPEEDS_KPH,
    MIN_R_ADVERSE_CAMBER_M,
    MIN_R_SUPERELEVATION_2_5_M,
    TABLE_ID,
)
from agent.tools.units import EXACT, format_decimal

CALC = Context(prec=28)
TWO_DP, ONE_DP = Decimal("0.01"), Decimal("0.1")

# CD 109 constants, named as printed.
AC_DUAL_BASE, AC_DUAL_DIVISOR = Decimal("6.6"), Decimal(10)  # Eq 2.2a
AC_SINGLE_BASE, AC_VISI_DIVISOR, AC_B_FACTOR = Decimal(12), Decimal(60), Decimal(2) / 45
FIGURE_2_1_AC_RANGE = (Decimal(0), Decimal(20))  # Figure 2.1's Ac axis
SINGLE_LOW_AC = Decimal("6.6")  # Figure 2.1: single c/ways below this are unusual
MIN_BENDINESS_LENGTH_KM = Decimal(2)  # Glossary: bendiness over a minimum length of 2 km
MAX_SIGHT_DISTANCE_M = Decimal(10_000)
MIN_VISI_READINGS = 20  # 2 km (cl. 2.6) at 100 m spacing (cl. 2.6.1)
VISI_LOG_BASE, VISI_VW_DIVISOR, VISI_B_DIVISOR = Decimal("2.46"), Decimal(25), Decimal(400)
EMPIRICAL_VISI_MAX_M = Decimal(720)  # Eq 2.8.2 NOTE 1
SUPERELEVATION_DIVISOR = Decimal("2.828")  # Eq 4.2
CAMBER_PERCENT = Decimal("2.5")  # cl. 4.1 and 4.1.1
MAX_SUPERELEVATION = {"rural": Decimal(7), "urban": Decimal(5)}  # cl. 4.3, 4.4
TRANSITION_DIVISOR = Decimal("46.7")  # Eq 4.13
Q_MAX, Q_ADVISED = Decimal("0.6"), Decimal("0.3")  # cl. 4.14 (shall), 4.14.1 (should)
ROOT_24R_FACTOR = Decimal(24)


MAX_SIGNIFICANT_DIGITS = 10


def _check_digits(number: Decimal) -> Decimal:
    digits = len("".join(map(str, number.as_tuple().digits)).rstrip("0"))
    if not number.is_finite() or digits > MAX_SIGNIFICANT_DIGITS:
        raise ValueError(f"{number} has too many digits: use at most {MAX_SIGNIFICANT_DIGITS}")
    return number


class _Args(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    @field_validator("*", mode="before")
    @classmethod
    def floats_via_str(cls, raw: object) -> object:
        # Decimal(0.1) would carry the binary error; Decimal("0.1") does not. A whole-number
        # float for an integer field ("design_speed_kph": 100.0) is accepted as that integer.
        if isinstance(raw, float):
            return int(raw) if raw.is_integer() and abs(raw) < 10**6 else str(raw)
        return raw

    @field_validator("*")
    @classmethod
    def bounded_digits(cls, value: object) -> object:
        if isinstance(value, Decimal):
            return _check_digits(value)
        if isinstance(value, tuple):
            return tuple(_check_digits(v) if isinstance(v, Decimal) else v for v in value)
        return value


DesignSpeed = Literal[120, 100, 85, 70, 60, 50]
_SPEED_DOC = f"Design speed in kph, one of {', '.join(map(str, DESIGN_SPEEDS_KPH))} (Table 2.10)"


class EquationResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    value: Decimal | None
    unit: str
    formula: str
    clauses: tuple[str, ...]  # chunk IDs the result relies on, e.g. "CD109-2.2"
    notes: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()

    @computed_field
    @property
    def needs_review(self) -> bool:
        return bool(self.warnings)

    @field_serializer("value")
    def plain_number(self, number: Decimal | None) -> str | None:
        # A string, not a float: the result goes back to the model as JSON.
        return None if number is None else format_decimal(number)


def _round(number: Decimal, step: Decimal) -> Decimal:
    return number.quantize(step, rounding=ROUND_HALF_UP, context=CALC)


# --- Design speed: bendiness, alignment constraint, harmonic mean visibility ----------------


class BendinessArgs(_Args):
    total_direction_change_deg: Decimal = Field(
        ge=0, le=100_000, description="Sum of the changes in direction along the road, degrees"
    )
    length_km: Decimal = Field(
        ge=Decimal("0.01"), le=1_000, description="Length the changes occur over, km"
    )


def bendiness(**kwargs: object) -> EquationResult:
    """B = total change in direction / length (CD 109 NOTE to cl. 2.2; Glossary)."""
    args = BendinessArgs.model_validate(kwargs)
    value = CALC.divide(args.total_direction_change_deg, args.length_km)
    warnings = ()
    if args.length_km < MIN_BENDINESS_LENGTH_KM:
        warnings = (
            f"Bendiness is measured over a minimum length of 2 km (Glossary); "
            f"{format_decimal(args.length_km)} km is shorter (cl. 2.4 for short improvements).",
        )
    return EquationResult(
        value=_round(value, ONE_DP),
        unit="degrees/km",
        formula="B = total change in direction (degrees) / length (km)",
        clauses=("CD109-2.2", "CD109-Glossary"),
        warnings=warnings,
    )


class AlignmentConstraintArgs(_Args):
    carriageway: Literal["dual", "single"]
    bendiness: Decimal = Field(ge=0, le=10_000, description="B, degrees/km")
    visi: Decimal | None = Field(
        default=None, gt=0, le=10_000, description="Harmonic mean visibility, m (single only)"
    )


def alignment_constraint(**kwargs: object) -> EquationResult:
    """Ac from Equation 2.2a (dual) or 2.2b (single), for reading Figure 2.1 (cl. 2.1, 2.2)."""
    args = AlignmentConstraintArgs.model_validate(kwargs)
    b = args.bendiness
    if args.carriageway == "dual":
        if args.visi is not None:
            raise ValueError("Equation 2.2a (dual carriageways) doesn't use VISI; omit it")
        value = CALC.add(AC_DUAL_BASE, CALC.divide(b, AC_DUAL_DIVISOR))
        formula = "Ac = 6.6 + B/10 (Equation 2.2a)"
    else:
        if args.visi is None:
            raise ValueError("Equation 2.2b (single carriageways) needs VISI")
        visi_term = CALC.divide(args.visi, AC_VISI_DIVISOR)
        value = CALC.add(CALC.subtract(AC_SINGLE_BASE, visi_term), CALC.multiply(AC_B_FACTOR, b))
        formula = "Ac = 12 - VISI/60 + 2B/45 (Equation 2.2b)"
    value = _round(value, TWO_DP)  # checks below use the value shown
    warnings = []
    low, high = FIGURE_2_1_AC_RANGE
    if not low <= value <= high:
        warnings.append("Ac is outside Figure 2.1's range (0 to 20): check the inputs.")
    if args.carriageway == "single" and value < SINGLE_LOW_AC:
        warnings.append(
            "Single carriageway Ac below 6.6 is only possible on long straight roads or with "
            "extensive visibility outside the highway (Figure 2.1)."
        )
    return EquationResult(
        value=value,
        unit="",
        formula=formula,
        clauses=("CD109-2.2", "CD109-2.1"),
        warnings=tuple(warnings),
    )


class HarmonicVisibilityArgs(_Args):
    sight_distances_m: tuple[Decimal, ...] = Field(
        min_length=1, max_length=10_000, description="Each measured sight distance, m"
    )

    @field_validator("sight_distances_m", mode="before")
    @classmethod
    def each_float_via_str(cls, raw: object) -> object:
        if isinstance(raw, list | tuple):
            return tuple(str(v) if isinstance(v, float) else v for v in raw)
        return raw

    @field_validator("sight_distances_m")
    @classmethod
    def all_positive(cls, values: tuple[Decimal, ...]) -> tuple[Decimal, ...]:
        if any(not 1 <= v <= MAX_SIGHT_DISTANCE_M for v in values):
            raise ValueError("every sight distance must be between 1 m and 10,000 m")
        return values


def harmonic_mean_visibility(**kwargs: object) -> EquationResult:
    """VISI = n / Σ(1/Vi), Equation 2.8.1, for new roads (cl. 2.8.1)."""
    args = HarmonicVisibilityArgs.model_validate(kwargs)
    readings = args.sight_distances_m
    reciprocal_sum = sum((CALC.divide(1, v) for v in readings), Decimal(0))
    value = CALC.divide(len(readings), reciprocal_sum)
    warnings = ()
    if len(readings) < MIN_VISI_READINGS:
        warnings = (
            f"Only {len(readings)} readings: VISI is measured over at least 2 km (cl. 2.6) at "
            "50 or 100 m intervals (cl. 2.6.1), which gives at least 20 per direction.",
        )
    return EquationResult(
        value=_round(value, ONE_DP),
        unit="m",
        formula="VISI = n / (1/V1 + 1/V2 + ... + 1/Vn) (Equation 2.8.1)",
        clauses=("CD109-2.8.1", "CD109-2.6", "CD109-2.6.1", "CD109-2.7"),
        notes=("Equation 2.8.1 is for new roads; existing roads use Equation 2.8.2 (cl. 2.8.2).",),
        warnings=warnings,
    )


class EmpiricalVisibilityArgs(_Args):
    verge_width_m: Decimal = Field(
        ge=0, le=100, description="Average verge width VW, averaged over both sides, m"
    )
    bendiness: Decimal = Field(ge=0, le=10_000, description="B, degrees/km over at least 2 km")


def empirical_visibility(**kwargs: object) -> EquationResult:
    """log10 VISI = 2.46 + VW/25 - B/400, Equation 2.8.2, for existing roads (cl. 2.8.2)."""
    args = EmpiricalVisibilityArgs.model_validate(kwargs)
    exponent = CALC.subtract(
        CALC.add(VISI_LOG_BASE, CALC.divide(args.verge_width_m, VISI_VW_DIVISOR)),
        CALC.divide(args.bendiness, VISI_B_DIVISOR),
    )
    value = _round(CALC.power(Decimal(10), exponent), ONE_DP)  # checked as shown
    warnings = ()
    if value > EMPIRICAL_VISI_MAX_M:
        warnings = (
            "Equation 2.8.2 applies up to VISI = 720 m (NOTE 1); this is beyond it, so measure "
            "VISI instead (Equation 2.8.1).",
        )
    return EquationResult(
        value=value,
        unit="m",
        formula="log10 VISI = 2.46 + VW/25 - B/400 (Equation 2.8.2)",
        clauses=("CD109-2.8.2",),
        notes=(
            "On long straight roads or with visibility outside the highway boundary, this can "
            "underestimate VISI (NOTE 2 to Equation 2.8.2).",
        ),
        warnings=warnings,
    )


# --- Horizontal alignment: superelevation and transitions -----------------------------------


class SuperelevationArgs(_Args):
    design_speed_kph: DesignSpeed = Field(description=_SPEED_DOC)
    radius_m: Decimal = Field(ge=1, le=100_000, description="Horizontal curve radius R, m")
    area: Literal["rural", "urban"]
    exception: Literal["existing road", "connector road loop"] | None = Field(
        default=None, description="Rural exceptions to the 7% maximum (cl. 4.3)"
    )


class SuperelevationResult(EquationResult):
    treatment: Literal["camber", "2.5% superelevation", "Equation 4.2"]
    formula_value: Decimal | None = None  # Equation 4.2 before any maximum is applied

    @field_serializer("formula_value")
    def plain_formula_value(self, number: Decimal | None) -> str | None:
        return None if number is None else format_decimal(number)


def superelevation(**kwargs: object) -> SuperelevationResult:
    """Crossfall treatment for a curve: camber, 2.5%, or Equation 4.2 with its maximum."""
    args = SuperelevationArgs.model_validate(kwargs)
    v, r = args.design_speed_kph, args.radius_m
    if args.area == "urban" and args.exception:
        raise ValueError("The cl. 4.3 exceptions apply to rural roads only")
    if r >= MIN_R_ADVERSE_CAMBER_M[v]:
        return SuperelevationResult(
            value=None,
            unit="%",
            formula="none: radius at or above Table 2.10 minimum R with adverse camber",
            treatment="camber",
            clauses=("CD109-4.1.1", TABLE_ID),
            notes=(
                "No superelevation needed. Crossfall or camber should be 2.5%, falling from the "
                "centre of single carriageways, or the central reserve of dual carriageways, to "
                "the outer channels (cl. 4.1.1). Eliminating adverse camber on larger radii can "
                "be needed for drainage (NOTE 2 to cl. 4.1.1).",
            ),
        )
    if r >= MIN_R_SUPERELEVATION_2_5_M[v]:
        return SuperelevationResult(
            value=CAMBER_PERCENT,
            unit="%",
            formula="2.5%: radius between Table 2.10 minimum R with 2.5% and with adverse camber",
            treatment="2.5% superelevation",
            clauses=("CD109-4.1", TABLE_ID),
            notes=("Falling towards the inside of the curve (cl. 4.1).",),
        )
    return _equation_4_2(args)


def _equation_4_2(args: SuperelevationArgs) -> SuperelevationResult:
    v, r = Decimal(args.design_speed_kph), args.radius_m
    raw = CALC.divide(CALC.multiply(v, v), CALC.multiply(SUPERELEVATION_DIVISOR, r))
    clauses = ["CD109-4.2", TABLE_ID]
    notes, warnings = [], []
    value = raw
    if args.exception:
        clauses.append("CD109-4.3")
        warnings.append(
            f"cl. 4.3 exception ({args.exception}): the 7% rural maximum doesn't apply, so "
            "confirm the superelevation with an engineer."
        )
    else:
        cap = MAX_SUPERELEVATION[args.area]
        clauses.append("CD109-4.3" if args.area == "rural" else "CD109-4.4")
        # Exact: S > cap  <=>  V² > 2.828 * cap * R, with no division or rounding.
        if EXACT.multiply(v, v) > EXACT.multiply(EXACT.multiply(SUPERELEVATION_DIVISOR, cap), r):
            value = cap
            notes.append(f"Equation 4.2 exceeds the {args.area} maximum, so {cap}% applies.")
    return SuperelevationResult(
        value=_round(value, TWO_DP),
        unit="%",
        formula="S = V² / (2.828 R) (Equation 4.2)",
        treatment="Equation 4.2",
        formula_value=_round(raw, TWO_DP),
        clauses=tuple(clauses),
        notes=tuple(notes),
        warnings=tuple(warnings),
    )


class TransitionArgs(_Args):
    design_speed_kph: DesignSpeed = Field(description=_SPEED_DOC)
    radius_m: Decimal = Field(ge=1, le=100_000, description="Horizontal curve radius R, m")
    q: Decimal = Field(
        ge=Decimal("0.01"), le=10, description="Rate of change of centripetal acceleration, m/s³"
    )


class TransitionResult(EquationResult):
    basic_length_m: Decimal  # Equation 4.13
    root_24r_m: Decimal  # √(24R)

    @field_serializer("basic_length_m", "root_24r_m")
    def plain_lengths(self, number: Decimal) -> str:
        return format_decimal(number)


def transition_length(**kwargs: object) -> TransitionResult:
    """Basic transition L = V³/(46.7qR), Equation 4.13, then the √(24R) comparison (cl. 4.15)."""
    args = TransitionArgs.model_validate(kwargs)
    v, r, q = Decimal(args.design_speed_kph), args.radius_m, args.q
    denominator = CALC.multiply(CALC.multiply(TRANSITION_DIVISOR, q), r)
    basic = CALC.divide(CALC.power(v, 3), denominator)
    root = CALC.sqrt(CALC.multiply(ROOT_24R_FACTOR, r))
    # Exact: L < √(24R)  <=>  V⁶ < 24R * (46.7qR)², all finite decimals.
    exact_denominator = EXACT.multiply(EXACT.multiply(TRANSITION_DIVISOR, q), r)
    basic_is_shorter = EXACT.power(v, 6) < EXACT.multiply(
        EXACT.multiply(ROOT_24R_FACTOR, r), EXACT.multiply(exact_denominator, exact_denominator)
    )
    clauses = ["CD109-4.13", "CD109-4.14", "CD109-4.14.1"]
    notes, warnings = [], []
    if basic_is_shorter:
        adopted, rule = basic, "the basic transition length shall be used (cl. 4.15)"
        clauses.append("CD109-4.15")
    else:
        adopted, rule = root, "√(24R) should be used (cl. 4.15.1)"
        clauses.append("CD109-4.15.1")
        notes.append("A √(24R) transition can give q above 0.6 m/s³ (NOTE 1 to cl. 4.15.1).")
    notes.append(f"L {'<' if basic_is_shorter else '>='} √(24R), so {rule}.")
    notes.append(
        "If the superelevation turnover needs more length, longer transitions should be "
        "provided (cl. 4.15.2)."
    )
    warnings += _q_warnings(q)
    if r >= MIN_R_ADVERSE_CAMBER_M[args.design_speed_kph]:
        clauses.append(TABLE_ID)
        notes.append(
            "Transitions are only required below Table 2.10's minimum R with adverse camber "
            "and without transitions (cl. 4.12); this radius is at or above it."
        )
    return TransitionResult(
        value=_round(adopted, ONE_DP),
        unit="m",
        formula="L = V³ / (46.7 q R) (Equation 4.13), compared with √(24R)",
        basic_length_m=_round(basic, ONE_DP),
        root_24r_m=_round(root, ONE_DP),
        clauses=("CD109-4.12", *clauses),
        notes=tuple(notes),
        warnings=tuple(warnings),
    )


def _q_warnings(q: Decimal) -> list[str]:
    if q > Q_MAX:
        return [f"q = {format_decimal(q)} exceeds the 0.6 m/s³ maximum (cl. 4.14, shall)."]
    if q > Q_ADVISED:
        return [f"q = {format_decimal(q)} is above the advised 0.3 m/s³ (cl. 4.14.1, should)."]
    return []
