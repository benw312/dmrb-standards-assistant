from decimal import Decimal

import pytest

from agent.tools.units import _UNITS, SUPPORTED_UNITS, UnitError, convert


@pytest.mark.parametrize(
    ("value", "from_unit", "to_unit", "expected"),
    [
        ("450", "mm", "m", "0.45"),
        ("0.5", "m", "mm", "500"),
        ("1.2", "km", "m", "1200"),
        ("85", "kph", "km/h", "85"),
        ("3.5", "%", "percent", "3.5"),
        ("7", "M", " m ", "7"),  # case and whitespace insensitive
    ],
)
def test_converts_within_a_dimension(value, from_unit, to_unit, expected):
    assert convert(Decimal(value), from_unit, to_unit) == Decimal(expected)


def test_conversion_is_exact_not_floating_point():
    # 0.1 + 0.2 style errors would make a value sitting on a limit fail.
    assert convert(Decimal("100"), "mm", "m") == Decimal("0.1")


def test_rejects_unknown_unit_with_supported_list():
    with pytest.raises(UnitError, match="Unknown unit 'furlong'.*mm"):
        convert(Decimal("1"), "furlong", "m")


def test_mph_is_not_supported_because_conversion_is_inexact():
    with pytest.raises(UnitError, match="Unknown unit 'mph'"):
        convert(Decimal("70"), "mph", "km/h")


def test_unit_literal_matches_conversion_table():
    # The Literal feeds the tool's JSON schema; the table does the work. They must agree.
    assert set(SUPPORTED_UNITS) == set(_UNITS)


def test_rejects_mixed_dimensions():
    with pytest.raises(UnitError, match="Cannot compare length .* with percent"):
        convert(Decimal("1"), "mm", "%")
