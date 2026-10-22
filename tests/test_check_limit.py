from decimal import Decimal

import pytest
from pydantic import ValidationError

from agent.tools.check_limit import CheckLimitArgs, check_limit
from agent.tools.units import SUPPORTED_UNITS

CLAUSE = "CD109-5.3.1"


@pytest.mark.parametrize(
    ("value", "comparison", "limit", "passed", "margin"),
    [
        ("6", "<=", "7", True, "1"),
        ("7", "<=", "7", True, "0"),
        ("7.5", "<=", "7", False, "-0.5"),
        ("7", "<", "7", False, "0"),
        ("6.9", "<", "7", True, "0.1"),
        ("2.5", ">=", "2.5", True, "0"),
        ("2", ">=", "2.5", False, "-0.5"),
        ("2.5", ">", "2.5", False, "0"),
        ("3", ">", "2.5", True, "0.5"),
    ],
)
def test_single_bound_comparisons(value, comparison, limit, passed, margin):
    result = check_limit(
        value=value, unit="%", comparison=comparison, limit=limit, source_clause=CLAUSE
    )

    assert result.passed is passed
    assert result.margin == Decimal(margin)


@pytest.mark.parametrize(
    ("value", "passed", "margin"),
    [
        ("2.5", True, "0"),  # inclusive lower bound
        ("5", True, "0"),  # inclusive upper bound
        ("3", True, "0.5"),  # margin is to the nearest bound
        ("4.5", True, "0.5"),
        ("2", False, "-0.5"),
        ("6", False, "-1"),
    ],
)
def test_between_is_inclusive_with_margin_to_nearest_bound(value, passed, margin):
    result = check_limit(
        value=value,
        unit="%",
        comparison="between",
        limit="2.5",
        upper_limit="5",
        source_clause=CLAUSE,
    )

    assert result.passed is passed
    assert result.margin == Decimal(margin)


def test_converts_value_into_the_limit_unit():
    result = check_limit(
        value="450",
        unit="mm",
        comparison=">=",
        limit="0.5",
        limit_unit="m",
        source_clause=CLAUSE,
    )

    assert result.passed is False
    assert result.value == Decimal("0.45")
    assert result.unit == "m"
    assert result.margin == Decimal("-0.05")


def test_float_inputs_do_not_introduce_binary_rounding():
    # JSON from the model arrives as floats; 0.1 + 0.2 must still equal 0.3 exactly.
    result = check_limit(value=0.3, unit="m", comparison="<=", limit=0.3, source_clause=CLAUSE)

    assert result.passed is True
    assert result.margin == Decimal("0")


def test_result_carries_source_clause_and_readable_explanation():
    result = check_limit(value="7.5", unit="%", comparison="<=", limit="7", source_clause=CLAUSE)

    assert result.source_clause == CLAUSE
    assert result.explanation == "FAIL: 7.5 % <= 7 % (exceeded by 0.5 %) [CD109-5.3.1]"


def test_strict_comparison_on_the_limit_says_so():
    result = check_limit(value="7", unit="%", comparison="<", limit="7", source_clause=CLAUSE)

    assert result.explanation == "FAIL: 7 % < 7 % (equals the limit) [CD109-5.3.1]"


def test_between_requires_upper_limit():
    with pytest.raises(ValidationError, match="upper_limit is required"):
        CheckLimitArgs(value=3, unit="%", comparison="between", limit=2.5, source_clause=CLAUSE)


def test_between_requires_upper_above_lower():
    with pytest.raises(ValidationError, match="must be greater than"):
        CheckLimitArgs(
            value=3,
            unit="%",
            comparison="between",
            limit=5,
            upper_limit=2.5,
            source_clause=CLAUSE,
        )


def test_upper_limit_only_allowed_with_between():
    with pytest.raises(ValidationError, match="only used with 'between'"):
        CheckLimitArgs(
            value=3, unit="%", comparison="<=", limit=5, upper_limit=6, source_clause=CLAUSE
        )


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), "abc"])
def test_rejects_non_finite_or_non_numeric_values(bad):
    with pytest.raises(ValidationError):
        CheckLimitArgs(value=bad, unit="%", comparison="<=", limit=5, source_clause=CLAUSE)


def test_rejects_unknown_comparison():
    with pytest.raises(ValidationError):
        CheckLimitArgs(value=3, unit="%", comparison="==", limit=5, source_clause=CLAUSE)


def test_requires_source_clause():
    with pytest.raises(ValidationError):
        CheckLimitArgs(value=3, unit="%", comparison="<=", limit=5, source_clause=" ")


def test_mixed_dimensions_are_a_validation_error():
    with pytest.raises(ValidationError, match="Cannot compare percent"):
        check_limit(
            value="3", unit="%", comparison="<=", limit="5", limit_unit="m", source_clause=CLAUSE
        )


@pytest.mark.parametrize("unit", ["furlong", "mph", ""])
def test_unknown_or_empty_unit_is_a_validation_error(unit):
    with pytest.raises(ValidationError):
        check_limit(
            value="3", unit="m", comparison="<=", limit="5", limit_unit=unit, source_clause=CLAUSE
        )


def test_units_are_case_insensitive():
    result = check_limit(value="3", unit=" MM ", comparison="<=", limit="5", source_clause=CLAUSE)

    assert result.unit == "mm"


def test_value_beyond_precision_is_rejected_not_rounded_into_a_pass():
    # 29 significant digits would round to exactly 1 in a 28-digit context and wrongly PASS.
    with pytest.raises(ValidationError, match="out of range"):
        check_limit(
            value="1.0000000000000000000000000001",
            unit="m",
            comparison="<=",
            limit="1",
            source_clause=CLAUSE,
        )


def test_smallest_meaningful_excess_still_fails():
    result = check_limit(
        value="1.00000000000001", unit="m", comparison="<=", limit="1", source_clause=CLAUSE
    )

    assert result.passed is False
    assert result.explanation == (
        "FAIL: 1.00000000000001 m <= 1 m (exceeded by 0.00000000000001 m) [CD109-5.3.1]"
    )


@pytest.mark.parametrize("huge", ["1E+999999999", "1e12", "1e-12"])
def test_extreme_magnitudes_are_rejected_with_a_clear_message(huge):
    with pytest.raises(ValidationError, match="out of range"):
        CheckLimitArgs(value=huge, unit="m", comparison="<=", limit=5, source_clause=CLAUSE)


def test_large_numbers_print_in_plain_notation():
    result = check_limit(value=1500, unit="m", comparison="<=", limit=1000, source_clause=CLAUSE)

    assert result.explanation == "FAIL: 1500 m <= 1000 m (exceeded by 500 m) [CD109-5.3.1]"


def test_negative_zero_prints_as_zero():
    result = check_limit(value=-0.0, unit="m", comparison="<=", limit=0, source_clause=CLAUSE)

    assert result.explanation == "PASS: 0 m <= 0 m (margin 0 m) [CD109-5.3.1]"


def test_result_serialises_numbers_as_exact_plain_strings():
    result = check_limit(
        value="450", unit="mm", comparison=">=", limit="0.5", limit_unit="m", source_clause=CLAUSE
    )

    dumped = result.model_dump(mode="json")

    assert dumped["value"] == "0.45"
    assert dumped["limit"] == "0.5"
    assert dumped["margin"] == "-0.05"
    assert dumped["upper_limit"] is None


def test_tool_schema_lists_allowed_units_and_comparisons():
    schema = CheckLimitArgs.model_json_schema()

    assert schema["properties"]["unit"]["enum"] == list(SUPPORTED_UNITS)
    assert schema["properties"]["comparison"]["enum"] == ["<=", "<", ">=", ">", "between"]
