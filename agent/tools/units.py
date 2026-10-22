"""Exact unit conversion for check_limit.

Conversion is arithmetic, and the model never does arithmetic, so it lives here.
Units only convert within one dimension; mixing dimensions is an error the agent sees.
Every factor is a power of ten, so conversion of a bounded Decimal is always exact.
"""

from decimal import Context, Decimal, DivisionByZero, Inexact, InvalidOperation, Overflow
from typing import Literal, get_args

Unit = Literal["mm", "m", "km", "%", "percent", "km/h", "kph"]
SUPPORTED_UNITS: tuple[str, ...] = get_args(Unit)

# unit -> (dimension, factor to the dimension's base unit). mph is deliberately absent: its
# factor (1.609344) makes km/h -> mph inexact, and DMRB design speeds are in km/h.
_UNITS: dict[str, tuple[str, Decimal]] = {
    "mm": ("length", Decimal("0.001")),
    "m": ("length", Decimal("1")),
    "km": ("length", Decimal("1000")),
    "%": ("percent", Decimal("1")),
    "percent": ("percent", Decimal("1")),
    "km/h": ("speed", Decimal("1")),
    "kph": ("speed", Decimal("1")),
}

# Any rounding raises instead of happening silently: a rounded number could flip a verdict.
EXACT = Context(prec=60, traps=[Inexact, Overflow, InvalidOperation, DivisionByZero])


class UnitError(ValueError):
    """Unknown unit, or units from different dimensions."""


def normalise(unit: str) -> Unit:
    key = unit.strip().lower()
    if key not in _UNITS:
        raise UnitError(f"Unknown unit {unit.strip()!r}. Supported: {', '.join(SUPPORTED_UNITS)}")
    return key  # type: ignore[return-value]  # membership checked above


def dimension(unit: str) -> str:
    return _UNITS[normalise(unit)][0]


def check_same_dimension(from_unit: str, to_unit: str) -> None:
    source, target = dimension(from_unit), dimension(to_unit)
    if source != target:
        raise UnitError(
            f"Cannot compare {source} ({from_unit.strip()}) with {target} ({to_unit.strip()})"
        )


def convert(value: Decimal, from_unit: str, to_unit: str) -> Decimal:
    check_same_dimension(from_unit, to_unit)
    source_factor = _UNITS[normalise(from_unit)][1]
    target_factor = _UNITS[normalise(to_unit)][1]
    return EXACT.divide(EXACT.multiply(value, source_factor), target_factor)
