"""Horizontal curvature radii from CD 109 Revision 1 Table 2.10, keyed by design speed (kph).

Clauses 4.1, 4.1.1, 4.2 and 4.12 are written against these radii ("radii less than those shown
in Table 2.10"). The clauses' own shorthand (V²/R > 5, V²/R > 7) is rounded: the table's
radii give V²/R between 6.8 and 7.08 for the 2.5% row, so the radii are what the tools use.
tests/test_design_speed_table.py checks these numbers against the indexed table when the
chunks are available locally.
"""

from decimal import Decimal

DESIGN_SPEEDS_KPH: tuple[int, ...] = (120, 100, 85, 70, 60, 50)

# "Minimum R* with adverse camber and without transitions" (V²/R = 5)
MIN_R_ADVERSE_CAMBER_M: dict[int, Decimal] = {
    120: Decimal(2880),
    100: Decimal(2040),
    85: Decimal(1440),
    70: Decimal(1020),
    60: Decimal(720),
    50: Decimal(520),
}

# "Minimum R* with superelevation of 2.5%" (V²/R = 7.07)
MIN_R_SUPERELEVATION_2_5_M: dict[int, Decimal] = {
    120: Decimal(2040),
    100: Decimal(1440),
    85: Decimal(1020),
    70: Decimal(720),
    60: Decimal(510),
    50: Decimal(360),
}

TABLE_ID = "CD109-Table-2.10"
