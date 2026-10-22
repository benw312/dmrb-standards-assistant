"""CD 109 equation tools. Expected values are worked by hand from the equations as printed in
CD 109 Revision 1 (© Crown copyright, OGL v3.0), or taken from the standard's own examples."""

import json
from decimal import Decimal
from pathlib import Path

import pytest
from pydantic import ValidationError

from agent.citations import normalise_citation
from agent.tools.design_speed_table import (
    DESIGN_SPEEDS_KPH,
    MIN_R_ADVERSE_CAMBER_M,
    MIN_R_SUPERELEVATION_2_5_M,
)
from agent.tools.equations import (
    alignment_constraint,
    bendiness,
    empirical_visibility,
    harmonic_mean_visibility,
    superelevation,
    transition_length,
)

CHUNKS = Path(__file__).resolve().parent.parent / "data" / "chunks"


# --- Bendiness and alignment constraint ------------------------------------------------------


def test_bendiness_matches_the_standards_worked_example():
    # NOTE to cl. 2.2: 3 km with a total change in direction of 180 degrees -> 60 degrees/km.
    result = bendiness(total_direction_change_deg=180, length_km=3)

    assert result.value == Decimal(60) and not result.needs_review


def test_bendiness_over_less_than_2_km_is_flagged():
    result = bendiness(total_direction_change_deg=90, length_km=1.5)

    assert result.value == Decimal(60) and "minimum length of 2 km" in result.warnings[0]


def test_alignment_constraint_dual_and_single():
    dual = alignment_constraint(carriageway="dual", bendiness=60)
    single = alignment_constraint(carriageway="single", bendiness=60, visi=300)

    assert dual.value == Decimal("12.60")  # 6.6 + 60/10
    assert single.value == Decimal("9.67")  # 12 - 300/60 + 2*60/45 = 9.666...
    assert "Equation 2.2b" in single.formula


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"carriageway": "dual", "bendiness": 60, "visi": 300}, "doesn't use VISI"),
        ({"carriageway": "single", "bendiness": 60}, "needs VISI"),
        ({"carriageway": "single", "bendiness": -1, "visi": 300}, "greater than or equal"),
    ],
)
def test_alignment_constraint_rejects_wrong_inputs(kwargs, message):
    with pytest.raises((ValueError, ValidationError), match=message):
        alignment_constraint(**kwargs)


def test_low_single_carriageway_ac_and_out_of_range_ac_are_flagged():
    low = alignment_constraint(carriageway="single", bendiness=0, visi=600)  # Ac = 2
    off_chart = alignment_constraint(carriageway="dual", bendiness=200)  # Ac = 26.6

    assert "only possible on long straight roads" in low.warnings[-1]
    assert "outside Figure 2.1" in off_chart.warnings[0]


# --- Harmonic mean visibility ----------------------------------------------------------------


def test_harmonic_mean_of_sight_distances():
    result = harmonic_mean_visibility(sight_distances_m=[100, 200, 300])

    assert result.value == Decimal("163.6")  # 3 / (1/100 + 1/200 + 1/300) = 163.63...


def test_twenty_equal_readings_give_that_reading_without_a_warning():
    result = harmonic_mean_visibility(sight_distances_m=[250.0] * 20)

    assert result.value == Decimal("250.0") and not result.needs_review


def test_few_readings_are_flagged_and_zero_is_refused():
    assert harmonic_mean_visibility(sight_distances_m=[100, 200]).needs_review
    with pytest.raises(ValidationError, match="between 1 m and 10,000 m"):
        harmonic_mean_visibility(sight_distances_m=[100, 0])


def test_empirical_visibility_and_its_720_m_limit():
    flat = empirical_visibility(verge_width_m=0, bendiness=0)  # 10^2.46
    wide = empirical_visibility(verge_width_m=12, bendiness=0)  # 10^2.94 = 871 m

    assert flat.value == Decimal("288.4") and not flat.needs_review
    assert wide.value == Decimal("871.0")
    assert "up to VISI = 720 m" in wide.warnings[0]


# --- Superelevation --------------------------------------------------------------------------


@pytest.mark.parametrize("speed", DESIGN_SPEEDS_KPH)
def test_superelevation_regimes_switch_at_table_2_10_radii(speed):
    camber_r, s25_r = MIN_R_ADVERSE_CAMBER_M[speed], MIN_R_SUPERELEVATION_2_5_M[speed]

    def treatment(radius):
        return superelevation(design_speed_kph=speed, radius_m=radius, area="rural").treatment

    assert treatment(camber_r) == "camber"  # at the minimum radius, adverse camber is allowed
    assert treatment(camber_r - Decimal("0.1")) == "2.5% superelevation"
    assert treatment(s25_r) == "2.5% superelevation"
    assert treatment(s25_r - Decimal("0.1")) == "Equation 4.2"


# Table 2.10 rows that state their superelevation: radius by design speed (120 ... 50).
TABLE_2_10_SUPERELEVATION_ROWS = {
    Decimal("3.5"): (1440, 1020, 720, 510, 360, 255),
    Decimal("5"): (1020, 720, 510, 360, 255, 180),
    Decimal("7"): (720, 510, 360, 255, 180, 127),
}


@pytest.mark.parametrize("stated", TABLE_2_10_SUPERELEVATION_ROWS)
def test_equation_4_2_reproduces_table_2_10s_superelevation_rows(stated):
    # An independent check on the formula: Table 2.10 prints rounded radii for 3.5%, 5% and
    # 7% superelevation, and Equation 4.2 at those radii gives the stated value to within 0.25.
    for speed, radius in zip(
        DESIGN_SPEEDS_KPH, TABLE_2_10_SUPERELEVATION_ROWS[stated], strict=True
    ):
        result = superelevation(design_speed_kph=speed, radius_m=radius, area="rural")
        assert abs(result.formula_value - stated) <= Decimal("0.25"), (speed, radius)


def test_superelevation_is_capped_at_7_rural_and_5_urban():
    rural = superelevation(design_speed_kph=120, radius_m=500, area="rural")  # 10.18% raw
    urban = superelevation(design_speed_kph=60, radius_m=200, area="urban")  # 6.36% raw

    assert (rural.value, rural.formula_value) == (Decimal("7.00"), Decimal("10.18"))
    assert "CD109-4.3" in rural.clauses
    assert urban.value == Decimal("5.00") and "CD109-4.4" in urban.clauses


def test_equation_value_below_the_cap_is_used_as_is():
    result = superelevation(design_speed_kph=120, radius_m=1000, area="rural")

    assert result.value == result.formula_value == Decimal("5.09")
    assert not result.notes


def test_rural_exception_lifts_the_cap_but_asks_for_review():
    result = superelevation(
        design_speed_kph=120, radius_m=500, area="rural", exception="existing road"
    )

    assert result.value == Decimal("10.18") and result.needs_review


def test_superelevation_refuses_bad_combinations():
    with pytest.raises(ValueError, match="rural roads only"):
        superelevation(design_speed_kph=60, radius_m=200, area="urban", exception="existing road")
    with pytest.raises(ValidationError):
        superelevation(design_speed_kph=90, radius_m=200, area="rural")  # not a Table 2.10 speed


# --- Transitions -----------------------------------------------------------------------------


def test_basic_transition_shorter_than_root_24r_is_used():
    result = transition_length(design_speed_kph=100, radius_m=720, q=0.3)

    # 100^3 / (46.7 * 0.3 * 720) = 99.13; sqrt(24 * 720) = 131.45
    assert (result.basic_length_m, result.root_24r_m) == (Decimal("99.1"), Decimal("131.5"))
    assert result.value == Decimal("99.1") and "CD109-4.15" in result.clauses


def test_root_24r_is_used_when_the_basic_length_is_longer():
    result = transition_length(design_speed_kph=50, radius_m=90, q=0.3)

    # basic = 125000 / (46.7 * 0.3 * 90) = 99.1 > sqrt(2160) = 46.5
    assert result.value == Decimal("46.5") and "CD109-4.15.1" in result.clauses


@pytest.mark.parametrize(("q", "fragment"), [(0.4, "advised 0.3"), (0.7, "0.6 m/s³ maximum")])
def test_q_above_the_limits_is_flagged(q, fragment):
    result = transition_length(design_speed_kph=100, radius_m=720, q=q)

    assert fragment in result.warnings[0]


def test_large_radius_notes_that_no_transition_is_required():
    result = transition_length(design_speed_kph=100, radius_m=2040, q=0.3)

    assert any("cl. 4.12" in n for n in result.notes)


# --- Output contract -------------------------------------------------------------------------


ALL_RESULTS = [
    bendiness(total_direction_change_deg=180, length_km=3),
    alignment_constraint(carriageway="single", bendiness=60, visi=300),
    harmonic_mean_visibility(sight_distances_m=[100, 200]),
    empirical_visibility(verge_width_m=3, bendiness=50),
    superelevation(design_speed_kph=120, radius_m=3000, area="rural"),
    superelevation(design_speed_kph=120, radius_m=500, area="rural"),
    transition_length(design_speed_kph=50, radius_m=90, q=0.3),
]


def test_results_serialise_numbers_as_strings_and_flag_review():
    payload = json.loads(ALL_RESULTS[5].model_dump_json())

    assert payload["value"] == "7" and payload["formula_value"] == "10.18"
    assert json.loads(ALL_RESULTS[2].model_dump_json())["needs_review"] is True


@pytest.mark.parametrize("result", ALL_RESULTS, ids=lambda r: r.formula[:20])
def test_cited_clauses_are_well_formed_chunk_ids(result):
    assert result.clauses
    assert all(normalise_citation(c) == c for c in result.clauses)


@pytest.mark.skipif(not (CHUNKS / "CD109.jsonl").exists(), reason="needs local data/chunks")
@pytest.mark.parametrize("result", ALL_RESULTS, ids=lambda r: r.formula[:20])
def test_cited_clauses_exist_in_the_chunks(result):
    ids = {json.loads(line)["citation_id"] for line in (CHUNKS / "CD109.jsonl").open()}

    assert set(result.clauses) <= ids


# --- Review fixes: boundaries and bounded inputs ----------------------------------------------


@pytest.mark.parametrize(
    ("verge", "shown", "warned"),
    [
        ("9.93", "719.8", False),  # just under 720 m
        ("9.9339", "720.0", False),  # 720.04 m: shown as 720.0, so no contradictory warning
        ("9.94", "720.4", True),  # just over
    ],
)
def test_empirical_visibility_warns_exactly_when_the_shown_value_exceeds_720(verge, shown, warned):
    result = empirical_visibility(verge_width_m=verge, bendiness=0)

    assert str(result.value) == shown and result.needs_review is warned


@pytest.mark.parametrize(
    "kwargs",
    [
        {"design_speed_kph": 120, "radius_m": "100.123456789012345678901234567", "q": "0.3"},
        {"design_speed_kph": 120, "radius_m": "1e-30", "q": "0.3"},
        {"design_speed_kph": 120, "radius_m": 100000, "q": "1e-30"},
        {"design_speed_kph": 120, "radius_m": "Infinity", "q": "0.3"},
    ],
    ids=["too-many-digits", "tiny-radius", "tiny-q", "infinite"],
)
def test_extreme_inputs_are_validation_errors_not_arithmetic_errors(kwargs):
    with pytest.raises(ValidationError):
        transition_length(**kwargs)


@pytest.mark.parametrize("distance", ["1e999999", 0.5, 20000])
def test_sight_distances_outside_1_to_10000_m_are_refused(distance):
    with pytest.raises(ValidationError):
        harmonic_mean_visibility(sight_distances_m=[100, distance])


def test_a_whole_number_float_design_speed_is_accepted():
    # JSON from a tool call can carry 100.0; 100.5 is still refused.
    assert transition_length(design_speed_kph=100.0, radius_m=720, q=0.3).value == Decimal("99.1")
    with pytest.raises(ValidationError):
        transition_length(design_speed_kph=100.5, radius_m=720, q=0.3)


def test_ac_checks_use_the_value_shown():
    # 12 - 337.8/60 + 0 = 6.37 shown; the low-Ac warning agrees with the shown value.
    result = alignment_constraint(carriageway="single", bendiness=0, visi="337.8")

    assert result.value == Decimal("6.37") and result.needs_review
